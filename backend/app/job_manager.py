from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, Optional

from flask import Blueprint, current_app, jsonify, request

JobFunction = Callable[[Dict[str, Any]], Any]


class AsyncJob:
    def __init__(self, function: JobFunction, context: Dict[str, Any], manager: JobManager) -> None:
        self._function = function
        self._context = context
        self._manager = manager
        self._job_id = str(uuid.uuid4())
        self._thread: Optional[threading.Thread] = None
        self._start_time: Optional[datetime] = None
        self._end_time: Optional[datetime] = None
        self._result: Any = None
        self._error: Optional[str] = None
        self._exception: Optional[BaseException] = None
        self._started = False
        self._done_event = threading.Event()
        self._created_at = datetime.utcnow()

    @property
    def job_id(self) -> str:
        return self._job_id

    @property
    def queued_at(self) -> datetime:
        return self._created_at

    @property
    def started_at(self) -> Optional[datetime]:
        return self._start_time

    @property
    def finished_at(self) -> Optional[datetime]:
        return self._end_time

    def start(self) -> None:
        if self._thread is not None:
            return

        app = self._manager.get_app()

        def runner() -> None:
            self._start_time = datetime.utcnow()
            try:
                if app is not None:
                    with app.app_context():
                        self._execute()
                else:
                    self._execute()
            finally:
                self._end_time = datetime.utcnow()
                self._done_event.set()
                self._manager._on_job_complete(self)

        self._started = True
        self._thread = threading.Thread(target=runner, name=f"AsyncJob-{self._job_id}", daemon=True)
        self._thread.start()

    def _execute(self) -> None:
        try:
            self._result = self._function(dict(self._context))
            self._error = None
            self._exception = None
        except BaseException as exc:  # pragma: no cover - defensive guard
            self._exception = exc
            self._error = str(exc) or exc.__class__.__name__
            self._result = None

    def is_busy(self) -> bool:
        return self._started and not self._done_event.is_set()

    def has_error(self) -> bool:
        return self._error is not None

    def get_result(self) -> Any:
        return self._result

    def get_error(self) -> Optional[str]:
        return self._error

    def get_exception(self) -> Optional[BaseException]:
        return self._exception

    def status(self) -> str:
        if not self._started:
            return "queued"
        if self.is_busy():
            return "busy"
        if self.has_error():
            return "error"
        return "done"


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, AsyncJob] = {}
        self._queue: Deque[str] = deque()
        self._lock = threading.Lock()
        self._active_job_id: Optional[str] = None
        self._app = None

    def attach_app(self, app) -> None:
        self._app = app

    def get_app(self):
        return self._app

    def start_job(self, function: JobFunction, context: Optional[Dict[str, Any]] = None) -> str:
        context_data = dict(context or {})
        job = AsyncJob(function=function, context=context_data, manager=self)
        start_immediately = False
        with self._lock:
            self._jobs[job.job_id] = job
            if self._active_job_id is None:
                self._active_job_id = job.job_id
                start_immediately = True
            else:
                self._queue.append(job.job_id)
        if start_immediately:
            job.start()
        return job.job_id

    def _on_job_complete(self, job: AsyncJob) -> None:
        next_job: Optional[AsyncJob] = None
        with self._lock:
            if self._active_job_id == job.job_id:
                self._active_job_id = None
                while self._queue:
                    next_id = self._queue.popleft()
                    candidate = self._jobs.get(next_id)
                    if candidate is not None:
                        self._active_job_id = next_id
                        next_job = candidate
                        break
        if next_job is not None:
            next_job.start()

    def get_job(self, job_id: str) -> Optional[AsyncJob]:
        return self._jobs.get(job_id)

    def describe_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.get_job(job_id)
        if job is None:
            return None
        data: Dict[str, Any] = {
            "job_id": job.job_id,
            "status": job.status(),
        }
        if job.queued_at:
            data["queued_at"] = job.queued_at.isoformat() + "Z"
        if job.started_at:
            data["started_at"] = job.started_at.isoformat() + "Z"
        if job.finished_at:
            data["finished_at"] = job.finished_at.isoformat() + "Z"
        if job.status() == "done":
            data["result"] = job.get_result()
        elif job.status() == "error":
            data["error"] = job.get_error() or "Job failed."
        return data


def get_job_manager(capp = None) -> JobManager:
    if capp is None:
        capp = current_app
    manager = capp.extensions.get("job_manager")
    if not isinstance(manager, JobManager):
        raise RuntimeError("Background job manager is unavailable.")
    return manager


bp = Blueprint("job_manager", __name__, url_prefix="/api")


@bp.get("/jobstatus")
def job_status() -> Any:
    job_id = request.args.get("id") or request.args.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id query parameter is required."}), 400
    manager = current_app.extensions.get("job_manager")
    if not isinstance(manager, JobManager):
        return jsonify({"error": "Job manager is unavailable."}), 503
    info = manager.describe_job(job_id)
    if info is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(info)
