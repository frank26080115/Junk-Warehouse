from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Mapping

from flask import Blueprint, current_app, jsonify, request

from .user_login import login_required
from services import maintenance

log = logging.getLogger(__name__)

bp = Blueprint("maintenance", __name__, url_prefix="/api")


def _ensure_datetime(value: Any) -> datetime:
    if value is None:
        raise ValueError("A cutoff datetime is required.")
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        tz = current_app.config.get("TZ")
        if tz is not None:
            dt = datetime.fromtimestamp(value, tz)
        else:
            dt = datetime.fromtimestamp(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("A cutoff datetime is required.")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid ISO datetime value: {raw}") from exc
    else:
        raise ValueError("Cutoff datetime must be a datetime, ISO string, or timestamp.")

    if dt.tzinfo is None:
        tz = current_app.config.get("TZ")
        if tz is not None:
            dt = dt.replace(tzinfo=tz)
    return dt


def _collect_parameters(payload: Mapping[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    nested = payload.get("parameters")
    if isinstance(nested, Mapping):
        params.update(nested)
    for key, value in payload.items():
        if key in {"task", "parameters"}:
            continue
        params.setdefault(key, value)
    return params


def _execute_task(task_name: str, params: Mapping[str, Any]):
    if task_name == "prune_deleted":
        return maintenance.prune_deleted()
    if task_name == "prune_stale_staging_items":
        cutoff_value = params.get("cutoff_date") or params.get("cutoff")
        cutoff = _ensure_datetime(cutoff_value)
        return maintenance.prune_stale_staging_items(cutoff)
    if task_name == "prune_stale_staging_invoices":
        cutoff_value = params.get("cutoff_date") or params.get("cutoff")
        cutoff = _ensure_datetime(cutoff_value)
        return maintenance.prune_stale_staging_invoices(cutoff)
    if task_name == "prune_images":
        target_raw = (
            params.get("target_directory")
            or params.get("directory")
            or params.get("path")
        )
        if target_raw is None:
            raise ValueError("The 'target_directory' parameter is required for prune_images.")
        target_text = str(target_raw).strip()
        if not target_text:
            raise ValueError("The 'target_directory' parameter is required for prune_images.")
        return maintenance.prune_images(target_text)
    raise ValueError(f"Unknown task '{task_name}'.")


@bp.post("/task")
@login_required
def run_task():
    data = request.get_json(silent=True)
    if not isinstance(data, Mapping):
        data = request.form.to_dict(flat=True)
    if not isinstance(data, Mapping) or not data:
        return jsonify({"error": "Missing request payload."}), 400

    task_name = str(data.get("task") or "").strip()
    if not task_name:
        return jsonify({"error": "Missing 'task' parameter."}), 400

    params = _collect_parameters(data)

    log.info("Running maintenance task %s", task_name)
    try:
        result = _execute_task(task_name, params)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - unexpected failure handling
        log.exception("Maintenance task %s failed", task_name)
        return jsonify({"error": "Task failed.", "details": str(exc)}), 500

    return jsonify({"task": task_name, "result": result}), 200
