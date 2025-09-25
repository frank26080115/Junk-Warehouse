# backend/app/items.py

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Union, List
import logging
import random
import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .user_login import login_required
from .db import get_engine, get_db_item_as_dict, update_db_row_by_dict
from .slugify import slugify
from .helpers import normalize_pg_uuid

log = logging.getLogger(__name__)

bp = Blueprint("items", __name__, url_prefix="/api")

TABLE = "items"
ID_COL = "id"


def get_item_thumbnail(item_uuid: Optional[str], *, db_session: Any = None) -> str:
    """
    Placeholder: return a thumbnail URL for an item.

    Parameters
    ----------
    item_uuid : Optional[str]
        Identifier for the item the thumbnail belongs to.
    db_session : Any, optional
        Optional database session/connection handle for future implementations.
    """

    # TODO: Implement lookup (e.g., join to images table and build public URL)
    return ""


def augment_item_dict(
    data: Mapping[str, Any],
    *,
    thumbnail_getter: Optional[Callable[[Optional[str]], str]] = None,
) -> Dict[str, Any]:
    """
    Convert an item row to a JSON-ready dict with derived fields.

    This helper normalizes UUIDs, generates the slug, attaches a thumbnail,
    and converts datetime objects to ISO strings.
    """

    out: Dict[str, Any] = dict(data)

    raw_item_uuid = out.get(ID_COL)
    if raw_item_uuid is not None:
        out[ID_COL] = str(raw_item_uuid)

    name = out.get("name")
    short_id = out.get("short_id")
    out["slug"] = slugify(name, short_id)

    getter = thumbnail_getter or (lambda uuid: get_item_thumbnail(uuid))
    out["thumbnail"] = getter(out.get(ID_COL))

    for key, value in list(out.items()):
        if hasattr(value, "isoformat"):
            try:
                out[key] = value.isoformat()
            except Exception:  # pragma: no cover - defensive guard
                pass

    return out


def _augment_item(d: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility wrapper that delegates to :func:`augment_item_dict`."""

    return augment_item_dict(d)


def _resolve_item_by_xyz(xyz: str) -> Optional[Dict[str, Any]]:
    """
    Resolve a front-end locator (id/slug/short-id/etc.) using the same search pipeline.
    - Calls search_items(raw_query=xyz) and returns the top hit, if any.
    - Returns None if nothing found.
    """
    if not xyz or not xyz.strip():
        return None

    # Use the search pipeline you already have; top result wins
    from .search import search_items  # local import to avoid circular dependency

    hits: List[Dict[str, Any]] = search_items(
        raw_query=xyz,
        target_uuid=None,
        context={"source": "getitem"},
    )
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

    # remove null creation date so the DB fills it in with now() automatically
    DEFAULTABLE_COLUMNS = {"date_creation", "date_last_modified", "textsearch"}  # add others as needed
    for col in list(payload):
        if col in DEFAULTABLE_COLUMNS:
            payload.pop(col)

    if payload.get("is_collection") is True:
        payload["is_container"] = True

    try:
        response_payload: Optional[Mapping[str, Any]] = None

        # Fuzzy update: lets the helper map keys without hardcoding column names here
        update_result = update_db_row_by_dict(
            engine=engine,
            table=TABLE,
            uuid=item_uuid,  # required for updates; your helper should error if missing/unresolvable
            data=payload,
            fuzzy=True,
            id_col_name=ID_COL,
        )

        if isinstance(update_result, tuple) and len(update_result) == 2:
            resp, status_code = update_result
            if status_code >= 400:
                if hasattr(resp, "get_json"):
                    return resp, status_code
                if isinstance(resp, Mapping):
                    return jsonify(resp), status_code
                return jsonify({"error": str(resp)}), status_code

            candidate_payload: Optional[Any] = None
            if hasattr(resp, "get_json"):
                try:
                    candidate_payload = resp.get_json(silent=True)
                except Exception:
                    candidate_payload = None
            elif isinstance(resp, Mapping):
                candidate_payload = resp

            if isinstance(candidate_payload, Mapping):
                response_payload = candidate_payload

        # Re-fetch authoritative row
        if item_uuid is None and response_payload is not None:
            returned_data = response_payload.get("data")
            if isinstance(returned_data, Mapping):
                possible_id = returned_data.get(ID_COL)
                if possible_id:
                    item_uuid = possible_id

        if not item_uuid and isinstance(payload, Mapping):
            # In a rare case where caller omitted id but your update helper still succeeded
            # (e.g., it resolved via short_id), try to get the id back from payload.
            item_uuid = payload.get(ID_COL) or item_uuid

        if not item_uuid:
            return jsonify({"error": "Save succeeded but item ID could not be determined"}), 500

        db_row = get_db_item_as_dict(engine, TABLE, item_uuid, id_col_name=ID_COL)
        if not db_row:
            return jsonify({"error": "Saved item not found"}), 404

        return jsonify(_augment_item(db_row)), 200

    except Exception as e:
        log.exception("saveitem failed")
        return jsonify({"error": str(e)}), 400


def insert_item(
    payload: Mapping[str, Any],
    *,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """
    Insert a new item row and return the augmented item dict.

    This is the logic that powers the ``/api/insertitem`` endpoint and can be
    re-used by offline scripts (e.g., CSV importers). ``is_staging`` defaults to
    ``True`` for new rows so that bulk imports never go live by accident, but an
    explicit ``False`` value from the caller is now respected.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("Item payload must be a mapping")

    data: Dict[str, Any] = dict(payload)
    engine = engine or get_engine()

    raw_uuid_val = data.get(ID_COL)
    normalized_uuid: Optional[str] = None
    if raw_uuid_val:
        try:
            normalized_uuid = normalize_pg_uuid(raw_uuid_val)
        except (ValueError, AttributeError, TypeError):
            log.debug(
                "insertitem: invalid id supplied; generating a new UUID",
                exc_info=False,
            )
    def _to_signed_32(value: int) -> int:
        value &= 0xFFFFFFFF
        return value - 0x100000000 if value >= 0x80000000 else value

    with engine.connect() as conn:
        def uuid_in_use(candidate_uuid: str) -> bool:
            result = conn.execute(
                text("SELECT 1 FROM items WHERE id = :item_id LIMIT 1"),
                {"item_id": candidate_uuid},
            )
            return result.first() is not None

        if normalized_uuid is None:
            generated_uuid: Optional[str] = None
            for _ in range(100):
                candidate_uuid = str(uuid.uuid4())
                if not uuid_in_use(candidate_uuid):
                    generated_uuid = candidate_uuid
                    break
            if generated_uuid is None:
                raise RuntimeError(
                    "Unable to allocate unique id after multiple attempts"
                )
            normalized_uuid = generated_uuid
        else:
            normalized_uuid = str(normalized_uuid)

        data[ID_COL] = normalized_uuid

        uuid_obj = uuid.UUID(str(normalized_uuid))
        preferred_unsigned = int(uuid_obj.hex[-8:], 16)
        preferred_short_id = _to_signed_32(preferred_unsigned)
        short_id_value = preferred_short_id

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

    data["short_id"] = short_id_value
    if data.get("is_staging") is None:
        # Default new rows to staging unless the caller explicitly provided
        # False.  (Truthiness is preserved; only ``None`` triggers the default.)
        data["is_staging"] = True

    if data.get("is_collection") is True:
        data["is_container"] = True

    # remove null creation date so the DB fills it in with now() automatically
    DEFAULTABLE_COLUMNS = {"date_creation", "date_last_modified", "textsearch"}  # add others as needed
    for col in list(data):
        if col in DEFAULTABLE_COLUMNS:
            data.pop(col)

    result = update_db_row_by_dict(
        engine=engine,
        table=TABLE,
        uuid="new",
        data=data,
        fuzzy=True,
        id_col_name=ID_COL,
    )

    if isinstance(result, tuple) and len(result) == 2:
        resp, status = result
        if status >= 400:
            detail: Optional[Any] = None
            if hasattr(resp, "get_json"):
                try:
                    detail = resp.get_json()
                except Exception:  # pragma: no cover - defensive
                    detail = None
            elif isinstance(resp, Mapping):
                detail = resp

            message = "database error during insert"
            if isinstance(detail, Mapping):
                extracted = detail.get("error") or detail.get("detail")
                if extracted:
                    message = str(extracted)
                else:
                    message = str(detail)
            raise RuntimeError(message)

    new_id = data.get(ID_COL)
    if not new_id:
        raise RuntimeError(
            "Insert succeeded but new item ID could not be determined"
        )

    db_row = get_db_item_as_dict(engine, TABLE, new_id, id_col_name=ID_COL)
    if not db_row:
        raise LookupError("Inserted item not found")

    return _augment_item(db_row)


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

    try:
        inserted = insert_item(raw_payload)
        return jsonify(inserted), 201
    except Exception as e:
        log.exception("insertitem failed")
        return jsonify({"error": str(e)}), 400

@bp.route("/autogenitems", methods=["POST"])
@login_required
def autogen_items_api():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, Mapping):
        return jsonify({"success": False, "error": "Request payload must be an object."}), 400

    invoice_value = (
        payload.get("invoice_uuid")
        or payload.get("invoiceId")
        or payload.get("invoice_id")
    )
    if not invoice_value:
        return jsonify({"success": False, "error": "Missing invoice UUID."}), 400
    try:
        invoice_uuid = normalize_pg_uuid(str(invoice_value))
    except Exception as exc:
        return jsonify({"success": False, "error": f"Invalid invoice UUID: {exc}"}), 400

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) == 0:
        return jsonify({"success": False, "error": "Items payload must be a non-empty list."}), 400

    engine = get_engine()
    succeeded_ids: List[str] = []
    failures: List[Dict[str, str]] = []

    for entry in raw_items:
        client_id = ""
        name_text = ""
        url_text = ""
        display_value = ""
        try:
            if not isinstance(entry, Mapping):
                raise TypeError("Each entry must be an object.")
            raw_client_id = entry.get("client_id")
            if isinstance(raw_client_id, str) and raw_client_id.strip():
                client_id = raw_client_id.strip()
            elif raw_client_id is not None:
                client_id = str(raw_client_id)

            raw_name = entry.get("name")
            raw_url = entry.get("url")
            if isinstance(raw_name, str):
                name_text = raw_name.strip()
            if isinstance(raw_url, str):
                url_text = raw_url.strip()

            display_value = name_text or url_text or client_id or "(unnamed entry)"

            if not name_text and not url_text:
                raise ValueError("Missing name and URL for auto-generated item.")

            row_payload: Dict[str, Any] = {
                "name": name_text or url_text or "(auto summary item)",
                "url": url_text,
                "remarks": "automatically generated from invoice",
                "is_staging": True,
            }

            # TODO: add pre-insert intelligence here
            insert_result = update_db_row_by_dict(
                engine=engine,
                table=TABLE,
                uuid="new",
                data=row_payload,
                fuzzy=True,
                id_col_name=ID_COL,
            )

            inserted_row: Optional[Mapping[str, Any]] = None
            if isinstance(insert_result, tuple) and len(insert_result) == 2:
                resp_obj, status_code = insert_result
                if status_code >= 400:
                    detail_payload: Optional[Mapping[str, Any]] = None
                    if hasattr(resp_obj, "get_json"):
                        try:
                            detail_payload = resp_obj.get_json(silent=True)
                        except Exception:
                            detail_payload = None
                    elif isinstance(resp_obj, Mapping):
                        detail_payload = resp_obj  # type: ignore[assignment]
                    message = ""
                    if isinstance(detail_payload, Mapping):
                        extracted = detail_payload.get("error") or detail_payload.get("detail")
                        if extracted:
                            message = str(extracted)
                    raise RuntimeError(message or "database error during insert")

                parsed_payload: Optional[Mapping[str, Any]] = None
                if hasattr(resp_obj, "get_json"):
                    try:
                        parsed_payload = resp_obj.get_json(silent=True)
                    except Exception:
                        parsed_payload = None
                elif isinstance(resp_obj, Mapping):
                    parsed_payload = resp_obj  # type: ignore[assignment]

                if isinstance(parsed_payload, Mapping):
                    data_section = parsed_payload.get("data")
                    if isinstance(data_section, Mapping):
                        inserted_row = data_section
            elif isinstance(insert_result, Mapping):
                possible_data = insert_result.get("data")
                if isinstance(possible_data, Mapping):
                    inserted_row = possible_data

            new_item_id: Optional[str] = None
            if inserted_row and ID_COL in inserted_row:
                new_item_id = str(inserted_row[ID_COL])
            if not new_item_id and row_payload.get(ID_COL):
                new_item_id = str(row_payload[ID_COL])
            if not new_item_id:
                raise RuntimeError("Insert succeeded but item ID could not be determined")

            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO invoice_items (item_id, invoice_id) VALUES (:item_id, :invoice_id)"
                    ),
                    {"item_id": new_item_id, "invoice_id": invoice_uuid},
                )
                # TODO: add post-insert intelligence here, maybe automatically linking up containers, maybe mark duplicates for merge

            succeeded_ids.append(client_id or new_item_id)
        except Exception as exc:
            error_message = str(exc)
            reference = display_value or url_text or name_text or client_id or "(unnamed entry)"
            log.exception("autogenitems insert failed for %s", reference)
            failures.append(
                {
                    "client_id": client_id,
                    "display": reference,
                    "error": error_message,
                }
            )

    response_payload: Dict[str, Any] = {
        "success": not failures,
        "invoice_uuid": invoice_uuid,
        "inserted_count": len(succeeded_ids),
        "succeeded_ids": succeeded_ids,
    }

    if not failures and succeeded_ids:
        count = len(succeeded_ids)
        plural = "" if count == 1 else "s"
        response_payload["message"] = f"Inserted {count} item{plural}."
    elif failures:
        summary = "; ".join(f"{item['display']}: {item['error']}" for item in failures)
        response_payload["failures"] = failures
        response_payload["message"] = summary
        response_payload["success"] = False

    return jsonify(response_payload), 200

