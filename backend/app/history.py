from __future__ import annotations

"""Utility helpers for recording history events in the database."""

from typing import Any, Dict, Mapping, Optional, Union

from sqlalchemy.engine import Engine

from app.db import get_engine, update_db_row_by_dict

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
        payload["item_id_1"] = item_id_1
    if item_id_2 is not None:
        payload["item_id_2"] = item_id_2
    if event is not None:
        payload["event"] = event

    prepared_meta = _prepare_meta(meta)
    if prepared_meta is not None:
        payload["meta"] = prepared_meta

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
