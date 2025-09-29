from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request

JobFunction = Callable[[Dict[str, Any]], Any]
RepeatableJobFunction = Callable[[], Any]


class RepeatableJob:
    """Represent a background task that should run repeatedly without extra context."""

    def __init__(self, name: str, function: RepeatableJobFunction, frequency: timedelta) -> None:
        if not name:
            raise ValueError("Repeatable jobs require a descriptive name.")
        if frequency.total_seconds() <= 0:
            raise ValueError("Repeatable job frequency must be a positive duration.")
        self._name = name
        self._function = function
        self._frequency = frequency
        self._last_completed: Optional[datetime] = None
        self._is_running = False
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def frequency(self) -> timedelta:
        return self._frequency

    @property
    def last_completed(self) -> Optional[datetime]:
        with self._lock:
            return self._last_completed

    def is_due(self, current_time: datetime) -> bool:
        """Determine whether the job should run at the provided moment."""
        with self._lock:
            if self._is_running:
                return False
            if self._last_completed is None:
                return True
            return current_time - self._last_completed >= self._frequency

    def mark_enqueued(self) -> bool:
        """Reserve the job for execution so it is not enqueued twice."""
        with self._lock:
            if self._is_running:
                return False
            self._is_running = True
            return True

    def cancel_pending(self) -> None:
        """Release the reservation when enqueueing fails."""
        with self._lock:
            self._is_running = False

    def build_job_callable(self) -> JobFunction:
        """Adapt the zero-argument repeatable function for the AsyncJob interface."""

        def runner(_context: Dict[str, Any]) -> Any:
            completed_successfully = False
            try:
                result = self._function()
                completed_successfully = True
                return result
            finally:
                with self._lock:
                    if completed_successfully:
                        self._last_completed = datetime.utcnow()
                    self._is_running = False

        return runner


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
        self._repeatable_jobs: List[RepeatableJob] = []
        self._repeatable_lock = threading.Lock()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._active_job_id: Optional[str] = None
        self._app = None
        self._logger = logging.getLogger(__name__)

    def attach_app(self, app) -> None:
        self._app = app

    def get_app(self):
        return self._app

    def install_repeatable_job(self, job: RepeatableJob) -> None:
        """Register a new repeatable job and ensure the scheduler is running."""
        with self._repeatable_lock:
            self._repeatable_jobs.append(job)
        self._ensure_scheduler_thread()

    def _ensure_scheduler_thread(self) -> None:
        """Start the scheduler loop once so repeatable jobs are evaluated regularly."""
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return
        self._scheduler_thread = threading.Thread(
            target=self._run_repeatable_jobs_loop,
            name="RepeatableJobScheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    def _run_repeatable_jobs_loop(self) -> None:
        while True:
            try:
                self._evaluate_repeatable_jobs()
            except Exception:
                self._logger.exception("Repeatable job scheduler encountered an unexpected error.")
            # Sleep for one minute between checks to avoid excessive polling.
            time.sleep(60)

    def _evaluate_repeatable_jobs(self) -> None:
        with self._repeatable_lock:
            jobs_snapshot = list(self._repeatable_jobs)
        current_time = datetime.utcnow()
        for job in jobs_snapshot:
            # Evaluate each job individually so long-running work does not block peers.
            if not job.is_due(current_time):
                continue
            if not job.mark_enqueued():
                continue
            try:
                # Enqueue the adapted callable so it participates in the regular AsyncJob queue.
                self.start_job(job.build_job_callable(), {})
            except Exception:
                job.cancel_pending()
                self._logger.exception('Failed to enqueue repeatable job "%s"', job.name)

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
