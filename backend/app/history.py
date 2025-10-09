from __future__ import annotations

"""Utility helpers for recording history events in the database."""

from typing import Any, Dict, List, Mapping, Optional, Union

import logging

from flask import Blueprint, jsonify, has_request_context, session
from sqlalchemy import text
from sqlalchemy.engine import Engine, RowMapping

from app.db import get_engine, get_or_create_session, update_db_row_by_dict
from app.helpers import normalize_pg_uuid, coerce_identifier_to_uuid, build_callstack_string
from .user_login import login_required
from automation.actor_context import get_actor_ctx

log = logging.getLogger(__name__)

bp = Blueprint("history", __name__, url_prefix="/api")
PAGE_SIZE = 100


def _prepare_meta(meta_value: Optional[Union[str, Mapping[str, Any]]]) -> Optional[str]:
    """Convert mapping metadata into a JSON string while respecting plain text."""
    if meta_value is None:
        return None
    if isinstance(meta_value, str):
        return meta_value
    # When a mapping is supplied, convert it to JSON for clarity and storage consistency.
    try:
        import json

        return json.dumps(dict(meta_value), ensure_ascii=False, sort_keys=True)
    except Exception:
        # Fall back to a descriptive string so the insert still succeeds.
        return str(meta_value)


def _resolve_username() -> Optional[str]:
    """Return the authenticated username when available.

    This helper gracefully handles non-request contexts so background
    jobs and unit tests can continue to log history without providing
    a username explicitly.
    """

    actor_context = get_actor_ctx()
    if isinstance(actor_context, Mapping):
        display_value = actor_context.get("display")
        if display_value is not None:
            candidate = str(display_value).strip()
            if candidate:
                return candidate
    if has_request_context():
        raw_username = session.get("user_id")  # type: ignore[index]
        if isinstance(raw_username, str):
            candidate = raw_username.strip()
            if candidate:
                return candidate
        elif raw_username is not None:
            # Some callers might stash alternate identifier types; str() keeps
            # the value readable while avoiding surprises.
            return str(raw_username)
    return None


def _summarize_item_name(raw_name: Optional[str]) -> Optional[str]:
    """Produce a concise preview of an item's name for the list view."""
    if not raw_name:
        return None
    trimmed = raw_name.strip()
    if not trimmed:
        return None
    if len(trimmed) <= 20:
        return trimmed
    return trimmed[:20]


def _serialize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Translate a database row into a JSON-friendly dictionary."""
    mapped: Mapping[str, Any] = row
    item_name_value = mapped.get("item_1_name")
    item_name = item_name_value if isinstance(item_name_value, str) else None
    date_value = mapped.get("date")
    if hasattr(date_value, "isoformat"):
        date_text = date_value.isoformat()  # type: ignore[assignment]
    else:
        date_text = str(date_value or "")
    return {
        "id": coerce_identifier_to_uuid(mapped.get("id")),
        "date": date_text,
        "username": str(mapped.get("username") or ""),
        "itemId1": coerce_identifier_to_uuid(mapped.get("item_id_1")),
        "itemId2": coerce_identifier_to_uuid(mapped.get("item_id_2")),
        "event": str(mapped.get("event") or ""),
        "meta": str(mapped.get("meta") or ""),
        "itemNamePreview": _summarize_item_name(item_name),
        "itemNameFull": item_name,
    }


def _query_history_rows(limit_value: int, offset_value: int) -> List[RowMapping]:
    """Retrieve history rows ordered from newest to oldest."""
    session_handle = get_or_create_session()
    statement = text(
        """
        SELECT
            h.id,
            h.date,
            h.username,
            h.item_id_1,
            h.item_id_2,
            h.event,
            h.meta,
            i.name AS item_1_name
        FROM history AS h
        LEFT JOIN items AS i ON i.id = h.item_id_1
        ORDER BY h.date DESC, h.id DESC
        LIMIT :limit_value
        OFFSET :offset_value
        """
    )
    result = session_handle.execute(statement, {"limit_value": limit_value, "offset_value": offset_value})
    return list(result.mappings())


@bp.get("/history")
@bp.get("/history/<int:page>")
@login_required
def fetch_history(page: int = 1):
    """Return a page of history entries for display in the UI."""
    try:
        parsed_page = int(page)
    except (TypeError, ValueError):
        parsed_page = 1
    if parsed_page < 1:
        parsed_page = 1

    page_size = PAGE_SIZE
    offset_value = (parsed_page - 1) * page_size

    try:
        rows = _query_history_rows(page_size + 1, offset_value)
    except Exception as exc:
        log.exception("Failed to retrieve history rows")
        return jsonify({"ok": False, "error": str(exc)}), 500

    has_next = len(rows) > page_size
    visible_rows = rows[:page_size]
    entries = [_serialize_row(row) for row in visible_rows]

    response_payload = {
        "ok": True,
        "page": parsed_page,
        "pageSize": page_size,
        "hasNext": has_next,
        "hasPrevious": parsed_page > 1,
        "entries": entries,
    }

    return jsonify(response_payload)


def log_history(
    *,
    item_id_1: Optional[str] = None,
    item_id_2: Optional[str] = None,
    event: Optional[str] = None,
    meta: Optional[Union[str, Mapping[str, Any]]] = None,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """Insert a row into the ``history`` table using the shared DB helper.

    Parameters are optional so callers can rely on database defaults when appropriate.
    Passing ``None`` for a value omits that column from the insert payload, allowing
    PostgreSQL to apply its default (for example, ``event`` defaults to an empty string).
    """
    try:
        active_engine = engine or get_engine()

        payload: Dict[str, Any] = {}

        # Only include keys that received meaningful values.
        if item_id_1 is not None:
            payload["item_id_1"] = normalize_pg_uuid(item_id_1)
        if item_id_2 is not None:
            payload["item_id_2"] = normalize_pg_uuid(item_id_2)
        if event is not None:
            payload["event"] = event

        prepared_meta = _prepare_meta(meta)
        if prepared_meta is not None:
            payload["meta"] = prepared_meta

        resolved_username = _resolve_username()
        if resolved_username is not None:
            # Persist the username when present so analysts can connect events
            # to specific operators. Leaving the column out keeps database
            # defaults intact for automated jobs that lack user context.
            payload["username"] = resolved_username

        callstack = build_callstack_string(1)
        payload["callstack"] = callstack

        # Delegate the actual insert to the established helper so that error handling and
        # RETURNING behaviour stay consistent with the rest of the codebase.
        response, status_code = update_db_row_by_dict(
            active_engine,
            "history",
            "new",
            payload,
            fuzzy=False,
        )

        # The helper returns a tuple of the response payload and HTTP-style status code. We
        # surface both so callers can inspect success/failure while reusing familiar shapes.
        return {"response": response, "status_code": status_code}
    except Exception as ex:
        log.exception("While calling log_history")
        return {"response": {"ok": False, "error": ex}, "status_code": 500}
