from __future__ import annotations

"""Utility helpers for recording history events in the database."""

from typing import Any, Dict, Mapping, Optional, Union

try:
    # Importing from Flask only when available ensures CLI utilities
    # can import this module without requiring a running Flask app.
    from flask import has_request_context, session  # type: ignore
except Exception:  # pragma: no cover - defensive fallback when Flask is absent
    has_request_context = lambda: False  # type: ignore
    session = {}  # type: ignore

from sqlalchemy.engine import Engine

from app.db import get_engine, update_db_row_by_dict
from app.helpers import normalize_pg_uuid

# We only import typing and database utilities so that this module remains focused on
# transforming Python data into a shape that the generic insert/update helper can use.

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
