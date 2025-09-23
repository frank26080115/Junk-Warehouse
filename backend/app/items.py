# backend/app/items.py

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union, List
import logging
import random
import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .user_login import login_required
from .db import get_engine, get_db_item_as_dict, update_db_row_by_dict
from .search import search_items  # reuse your search to resolve xyz
from .slugify import slugify

log = logging.getLogger(__name__)

bp = Blueprint("items", __name__, url_prefix="/api")

TABLE = "items"
ID_COL = "id"


def _get_thumbnail_for_item(item_uuid: str) -> str:
    """
    Placeholder: return a thumbnail URL for an item.
    TODO: Implement actual lookup (e.g., images.rank = 0) and build a public URL.
    """
    # TODO
    return ""


def _augment_item(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add computed fields (slug, thumbnail) and normalize types for JSON.
    Mirrors the behavior used in search results.
    """
    out = dict(d)

    # Normalize UUID to string for JSON
    if ID_COL in out and out[ID_COL] is not None:
        out[ID_COL] = str(out[ID_COL])

    # Compute slug (best-effort; short_id may be None/0)
    name = out.get("name") or ""
    short_id = out.get("short_id") or 0
    out["slug"] = slugify(name, short_id)

    # Thumbnail
    out["thumbnail"] = _get_thumbnail_for_item(out.get(ID_COL, ""))

    # Datetime normalization (if your get_db_item_as_dict returns datetimes)
    for k, v in list(out.items()):
        # Very light-touch: ISO-ify datetimes if needed
        if hasattr(v, "isoformat"):
            try:
                out[k] = v.isoformat()
            except Exception:
                pass

    return out


def _resolve_item_by_xyz(xyz: str) -> Optional[Dict[str, Any]]:
    """
    Resolve a front-end locator (id/slug/short-id/etc.) using the same search pipeline.
    - Calls search_items(raw_query=xyz) and returns the top hit, if any.
    - Returns None if nothing found.
    """
    if not xyz or not xyz.strip():
        return None

    # Use the search pipeline you already have; top result wins
    hits: List[Dict[str, Any]] = search_items(raw_query=xyz, target_uuid=None, context={"source": "getitem"})
    if not hits:
        return None

    return hits[0]


@bp.route("/getitem", methods=["POST"])
@login_required
def get_item_api():
    """
    POST /api/getitem
    Body: { "xyz": string }  # could be "id/slug/short-id" — backend resolves via search
    Returns: the resolved item JSON object (augmented), or 404 if not found.
    """
    data = request.get_json(silent=True) or {}
    xyz = (data.get("xyz") or "").strip()

    if not xyz:
        return jsonify({"error": "Missing 'xyz'"}), 400

    # Resolve using search, then fetch authoritative row by UUID if you want
    top = _resolve_item_by_xyz(xyz)
    if not top:
        return jsonify({"error": "Item not found"}), 404

    # If you prefer to round-trip to DB for fresh values, use id from top hit:
    item_uuid = top.get(ID_COL)
    if not item_uuid:
        return jsonify({"error": "Item not found"}), 404

    engine: Engine = get_engine()
    try:
        db_row = get_db_item_as_dict(engine, TABLE, item_uuid, id_col_name=ID_COL)
        if not db_row:
            return jsonify({"error": "Item not found"}), 404
        return jsonify(_augment_item(db_row)), 200
    except Exception as e:
        log.exception("getitem failed")
        return jsonify({"error": str(e)}), 400


@bp.route("/saveitem", methods=["POST"])
@login_required
def save_item_api():
    """
    POST /api/saveitem
    Body: full/partial item JSON. Must include a resolvable primary key (id) OR
          a combination you’ve decided your update function can use.
    Returns: updated item JSON (augmented).
    """
    payload: Union[str, Mapping[str, Any], Any] = request.get_json(silent=True) or {}
    if not payload:
        return jsonify({"error": "Missing item payload"}), 400

    engine: Engine = get_engine()

    # Try to pull the UUID out of the payload if present
    item_uuid = None
    if isinstance(payload, Mapping):
        item_uuid = payload.get(ID_COL) or None

    try:
        # Fuzzy update: lets the helper map keys without hardcoding column names here
        update_db_row_by_dict(
            engine=engine,
            table=TABLE,
            uuid=item_uuid,  # required for updates; your helper should error if missing/unresolvable
            data=payload,
            fuzzy=True,
            id_col_name=ID_COL,
        )

        # Re-fetch authoritative row
        if not item_uuid and isinstance(payload, Mapping):
            # In a rare case where caller omitted id but your update helper still succeeded
            # (e.g., it resolved via short_id), try to get the id back from payload.
            item_uuid = payload.get(ID_COL) or item_uuid

        if not item_uuid:
            # As a fallback, try resolving by name/short_id from payload (best-effort)
            # This is defensive; ideally clients provide id on save.
            name = (payload.get("name") or "").strip() if isinstance(payload, Mapping) else ""
            short_id = payload.get("short_id") if isinstance(payload, Mapping) else None
            guess = None
            if name:
                guess = _resolve_item_by_xyz(name)
            if not guess and short_id is not None:
                guess = _resolve_item_by_xyz(str(short_id))
            if guess:
                item_uuid = guess.get(ID_COL)

        if not item_uuid:
            return jsonify({"error": "Save succeeded but item ID could not be determined"}), 500

        db_row = get_db_item_as_dict(engine, TABLE, item_uuid, id_col_name=ID_COL)
        if not db_row:
            return jsonify({"error": "Saved item not found"}), 404

        return jsonify(_augment_item(db_row)), 200

    except Exception as e:
        log.exception("saveitem failed")
        return jsonify({"error": str(e)}), 400


@bp.route("/insertitem", methods=["POST"])
@login_required
def insert_item_api():
    """
    POST /api/insertitem
    Body: item JSON (can be partial; defaults handled in DB).
    Returns: the inserted item JSON (augmented).
    """
    raw_payload: Union[str, Mapping[str, Any], Any] = request.get_json(silent=True) or {}
    if not raw_payload:
        return jsonify({"error": "Missing item payload"}), 400
    if not isinstance(raw_payload, Mapping):
        return jsonify({"error": "Item payload must be an object"}), 400

    payload: Dict[str, Any] = dict(raw_payload)

    engine: Engine = get_engine()
    try:
        raw_uuid_val = payload.get(ID_COL)
        normalized_uuid: Optional[str] = None
        if raw_uuid_val:
            try:
                normalized_uuid = str(uuid.UUID(str(raw_uuid_val)))
            except (ValueError, AttributeError, TypeError):
                log.debug("insertitem: invalid id supplied; generating a new UUID", exc_info=False)
        if normalized_uuid is None:
            normalized_uuid = str(uuid.uuid4())
        payload[ID_COL] = normalized_uuid

        def _to_signed_32(value: int) -> int:
            value &= 0xFFFFFFFF
            return value - 0x100000000 if value >= 0x80000000 else value

        uuid_obj = uuid.UUID(normalized_uuid)
        preferred_unsigned = int(uuid_obj.hex[-8:], 16)
        preferred_short_id = _to_signed_32(preferred_unsigned)
        short_id_value = preferred_short_id

        with engine.connect() as conn:
            def short_id_in_use(candidate: int) -> bool:
                result = conn.execute(
                    text("SELECT 1 FROM items WHERE short_id = :sid LIMIT 1"),
                    {"sid": candidate},
                )
                return result.first() is not None

            if short_id_in_use(short_id_value):
                log.debug(
                    "short_id %d already in use; generating random replacement",
                    short_id_value,
                )
                rng = random.SystemRandom()
                unique_found = False
                for _ in range(100):
                    candidate_unsigned = rng.getrandbits(32)
                    short_id_value = _to_signed_32(candidate_unsigned)
                    if not short_id_in_use(short_id_value):
                        unique_found = True
                        break
                if not unique_found:
                    raise RuntimeError(
                        "Unable to allocate unique short_id after multiple attempts"
                    )

        payload["short_id"] = short_id_value

        # Insert path: tell helper explicitly to insert a new row
        result = update_db_row_by_dict(
            engine=engine,
            table=TABLE,
            uuid="new",
            data=payload,
            fuzzy=True,
            id_col_name=ID_COL,
        )

        if isinstance(result, tuple) and len(result) == 2:
            resp, status = result
            if status >= 400:
                return resp, status

        # Try to determine the new item's id:
        new_id = payload.get(ID_COL)

        if not new_id:
            # Resolve by something stable from payload (best-effort):
            # Prefer name + short_id if available; otherwise try name alone.
            name = (payload.get("name") or "").strip()
            short_id = payload.get("short_id")

            guess = None
            if name and short_id is not None:
                guess = _resolve_item_by_xyz(f"{name} {short_id}")
            if not guess and name:
                guess = _resolve_item_by_xyz(name)
            if not guess and short_id is not None:
                guess = _resolve_item_by_xyz(str(short_id))

            if guess:
                new_id = guess.get(ID_COL)

        if not new_id:
            return jsonify({"error": "Insert succeeded but new item ID could not be determined"}), 500

        db_row = get_db_item_as_dict(engine, TABLE, new_id, id_col_name=ID_COL)
        if not db_row:
            return jsonify({"error": "Inserted item not found"}), 404

        return jsonify(_augment_item(db_row)), 201

    except Exception as e:
        log.exception("insertitem failed")
        return jsonify({"error": str(e)}), 400
