# backend/app/items.py

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Union, List, cast
from datetime import datetime, timedelta, timezone
import logging
import random
import uuid

from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from .user_login import login_required
from .db import get_engine, get_db_item_as_dict, update_db_row_by_dict, unwrap_db_result, get_or_create_session
from .embeddings import update_embeddings_for_item
from .slugify import slugify
from .helpers import normalize_pg_uuid, parse_tagged_text_to_dict, clean_item_name
from .metatext import update_metatext
from .static_server import get_public_html_path
from .assoc_helper import (
    CONTAINMENT_BIT,
    get_item_relationship,
    move_item,
    set_item_relationship,
)
from .containment_path import fetch_containment_paths, get_all_containments
from .image_handler import (
    store_image_for_item,
    BadRequest as ImageBadRequest,
    UnsupportedMedia as ImageUnsupportedMedia,
)
from .job_manager import get_job_manager
from app.config_loader import get_pin_open_expiry_hours
from .history import log_history
from .tree_browse import get_root_structure

log = logging.getLogger(__name__)

bp = Blueprint("items", __name__, url_prefix="/api")
# The items blueprint groups together every endpoint that interacts with item
# records.  Consolidating the registration point makes it easier to understand
# how the API surface is organized when reading the module for the first time.


TABLE = "items"
ID_COL = "id"

@bp.get("/getinittree")
@login_required
def get_initial_tree() -> tuple[Any, int]:
    """Return the initial tree data for the containment browser."""

    try:
        tree_structure = get_root_structure()
    except Exception as exc:
        log.exception("Unable to build initial tree structure")
        return jsonify({"error": str(exc)}), 500

    return jsonify(tree_structure), 200


def _fetch_recent_pin_ids(conn: Any, table_name: str) -> List[str]:
    """Return normalized UUID strings for the currently pinned rows."""

    from .search import append_pinned_items  # Local import avoids circular dependency.

    pinned_rows: List[Dict[str, Any]] = []
    append_pinned_items(conn, table_name, pinned_rows)

    normalized_ids: List[str] = []
    for row in pinned_rows:
        raw_identifier = row.get("id")
        if not raw_identifier:
            continue
        try:
            # UUID columns sometimes arrive as UUID objects and sometimes as
            # strings, so we coerce them to text before normalizing to avoid
            # subtle mismatches in downstream comparisons.
            normalized_ids.append(normalize_pg_uuid(raw_identifier))
        except (ValueError, TypeError, AttributeError):
            log.debug("Skipping pinned %s with invalid id: %r", table_name, raw_identifier)
    return normalized_ids


def _ensure_containment_relationship(conn: Any, source_id: str, target_id: str) -> None:
    """Create or update the containment relationship between two records."""

    if not source_id or not target_id or source_id == target_id:
        return

    query_parameters = {"item_id": source_id, "assoc_id": target_id}
    existing = conn.execute(
        text(
            """
            SELECT id, assoc_type
            FROM relationships
            WHERE item_id = :item_id
              AND assoc_id = :assoc_id
            LIMIT 1
            """
        ),
        query_parameters,
    ).mappings().first()

    if existing:
        current_bits = int(existing.get("assoc_type") or 0)
        # Combine the existing association flags with the containment bit so we preserve
        # any previously established relationships while guaranteeing containment is set.
        desired_bits = current_bits | CONTAINMENT_BIT
        if desired_bits != current_bits:
            conn.execute(
                text(
                    """
                    UPDATE relationships
                    SET assoc_type = :assoc_type
                    WHERE id = :relationship_id
                    """
                ),
                {
                    "assoc_type": desired_bits,
                    "relationship_id": existing.get("id"),
                },
            )
        return

    conn.execute(
        text(
            """
            INSERT INTO relationships (item_id, assoc_id, assoc_type)
            VALUES (:item_id, :assoc_id, :assoc_type)
            """
        ),
        {
            "item_id": source_id,
            "assoc_id": target_id,
            "assoc_type": CONTAINMENT_BIT,
        },
    )
def _synchronize_pinned_relationships(
    engine: Engine,
    *,
    source_item_id: Optional[str],
    include_invoices: bool = True,
) -> None:
    """Ensure the new item points to any currently pinned entities."""

    if not source_item_id:
        return

    try:
        normalized_source = normalize_pg_uuid(source_item_id)
    except (ValueError, TypeError, AttributeError):
        log.debug("Unable to normalize source item id for relationship sync: %r", source_item_id)
        return

    with engine.begin() as conn:
        # Ensure every currently pinned item is marked as contained by the new
        # source record.  This keeps the user-facing containment tree accurate
        # when several entities are pinned before creating a new item.
        pinned_item_ids = _fetch_recent_pin_ids(conn, "items")
        for pinned_item_id in pinned_item_ids:
            _ensure_containment_relationship(conn, normalized_source, pinned_item_id)

        if include_invoices:
            # Invoice pins should also be linked to the new item when the
            # caller opts in.  The invoices list is tracked separately so we
            # can reuse the helper for both tables without duplicating logic.
            pinned_invoice_ids = _fetch_recent_pin_ids(conn, "invoices")
            for pinned_invoice_id in pinned_invoice_ids:
                _ensure_containment_relationship(conn, normalized_source, pinned_invoice_id)


def _build_thumbnail_public_url(dir_value: Any, file_name: Any) -> Optional[str]:
    """Resolve a browser-accessible URL for either a thumbnail or the original image."""

    raw_name = str(file_name or "").strip()
    if not raw_name:
        return None

    raw_dir = str(dir_value or "").strip()
    safe_dir = raw_dir.strip("/\\")
    safe_name = raw_name.lstrip("/\\")

    def _split_segments(value: str) -> List[str]:
        """Normalize a path-like string into safe URL segments."""
        sanitized = value.replace("\\", "/")
        return [segment for segment in sanitized.split("/") if segment]

    dir_segments = _split_segments(safe_dir)
    name_segments = _split_segments(safe_name)
    if not name_segments:
        return None

    base_path = get_public_html_path()

    def _build_path(segments: List[str]) -> Any:
        """Construct an absolute path beneath the public HTML root."""
        current = base_path
        for part in segments:
            current = current / part
        return current

    base_segments = ["imgs"] + dir_segments + name_segments
    selected_segments = list(base_segments)
    selected_path = _build_path(selected_segments)

    # Prefer a dedicated thumbnail when it exists beside the original image.
    file_segment = name_segments[-1]
    dot_index = file_segment.rfind(".")
    if dot_index != -1:
        thumbnail_file = f"{file_segment[:dot_index]}.thumbnail{file_segment[dot_index:]}"
    else:
        thumbnail_file = f"{file_segment}.thumbnail"
    thumbnail_segments = base_segments[:-1] + [thumbnail_file]
    thumbnail_path = _build_path(thumbnail_segments)

    if thumbnail_path.exists():
        selected_segments = thumbnail_segments
        selected_path = thumbnail_path

    try:
        relative = selected_path.relative_to(base_path)
        return "/" + "/".join(relative.parts)
    except ValueError:
        return "/" + "/".join(selected_segments)


def _query_item_thumbnails(session: Any, item_ids: List[str]) -> Dict[str, str]:
    """Fetch thumbnail (or fallback image) URLs for the given item identifiers."""

    if not item_ids:
        return {}

    thumb_sql = text(
        """
        SELECT DISTINCT ON (ii.item_id)
            ii.item_id,
            img.dir,
            img.file_name,
            ii.rank,
            img.date_updated,
            img.id AS image_id
        FROM item_images AS ii
        JOIN images AS img ON img.id = ii.img_id
        WHERE NOT img.is_deleted
          AND ii.item_id IN :item_ids
        ORDER BY
            ii.item_id,
            ii.rank ASC,
            img.date_updated DESC,
            img.id ASC
        """
    ).bindparams(bindparam("item_ids", expanding=True))

    rows = session.execute(thumb_sql, {"item_ids": item_ids}).mappings().all()

    thumbnails: Dict[str, str] = {}
    for row in rows:
        identifier = row.get("item_id")
        if identifier is None:
            continue
        url = _build_thumbnail_public_url(row.get("dir"), row.get("file_name"))
        if not url:
            continue
        thumbnails[str(identifier)] = url

    return thumbnails


def get_item_thumbnails(
    item_ids: Iterable[Optional[str]],
    *,
    db_session: Any = None
) -> Dict[str, str]:
    """Return a mapping of item ids to thumbnail URLs.

    The lookup gracefully falls back to the original image when a dedicated
    thumbnail file is missing, ensuring callers always receive a usable URL.
    """

    unique_ids: List[str] = []
    seen: set[str] = set()
    for raw in item_ids:
        if not raw:
            continue
        value = str(raw)
        if value in seen:
            continue
        seen.add(value)
        unique_ids.append(value)

    if not unique_ids:
        return {}

    session = db_session or get_or_create_session()
    return _query_item_thumbnails(session, unique_ids)


def get_item_thumbnail(item_uuid: Optional[str], *, db_session: Any = None) -> str:
    """Return the best thumbnail URL for a specific item.

    When the expected thumbnail asset cannot be located, the caller receives
    the original image path instead of an empty string.
    """

    if not item_uuid:
        return ""

    normalized = str(item_uuid)
    thumbnails = get_item_thumbnails([normalized], db_session=db_session)
    return thumbnails.get(normalized, "")

def augment_item_dict(
    data: Mapping[str, Any],
    *,
    thumbnail_getter: Optional[Callable[[Optional[str]], str]] = None,
    inc_containments: bool = False,
) -> Dict[str, Any]:
    """
    Convert an item row to a JSON-ready dict with derived fields.

    This helper normalizes UUIDs, generates the slug, attaches a thumbnail,
    and converts datetime objects to ISO strings.  When ``inc_containments`` is
    enabled, the response also lists every containment relationship discovered
    by :func:`get_all_containments`.
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

    pin_opened_value = out.get("pin_as_opened")
    if pin_opened_value is not None:
        pin_opened_moment: Optional[datetime] = None
        if hasattr(pin_opened_value, "isoformat"):
            pin_opened_moment = cast(datetime, pin_opened_value)
        elif isinstance(pin_opened_value, str):
            try:
                pin_opened_moment = datetime.fromisoformat(pin_opened_value)
            except ValueError:
                pin_opened_moment = None

        if pin_opened_moment is not None:
            if pin_opened_moment.tzinfo is None:
                pin_opened_moment = pin_opened_moment.replace(tzinfo=timezone.utc)

            try:
                active_config = current_app.config
            except RuntimeError:
                # No active Flask application context, so fall back to the static loader.
                configured_hours = get_pin_open_expiry_hours()
            else:
                # Use the live application configuration when a request context is present.
                configured_hours = get_pin_open_expiry_hours(active_config)

            expiry_threshold = datetime.now(timezone.utc) - timedelta(hours=configured_hours)

            if pin_opened_moment < expiry_threshold:
                # Present expired pins as cleared so the caller sees the same behaviour
                # as the database-level filtering without mutating the stored value.
                out["pin_as_opened"] = None

    for key, value in list(out.items()):
        if hasattr(value, "isoformat"):
            try:
                out[key] = value.isoformat()
            except Exception:  # pragma: no cover - defensive guard
                pass

    if inc_containments:
        item_identifier = out.get(ID_COL)
        if item_identifier:
            try:
                containment_ids = get_all_containments(item_identifier)
            except Exception as exc:
                log.debug("Unable to load containment ids for %s: %s", item_identifier, exc)
                containment_ids = []
            out["containments"] = containment_ids

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
    include_containments = bool(data.get("inc_containments"))

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
        return jsonify(augment_item_dict(db_row, inc_containments=include_containments)), 200
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

    if payload.get("is_collection") is True or payload.get("is_tree_root") is True:
        # Ensure special groupings (collections and tree roots) always count as containers
        payload["is_container"] = True

    before_item = None
    try:
        before_item = get_db_item_as_dict(engine, TABLE, item_uuid, id_col_name=ID_COL)
    except Exception:
        log.exception("for history logging, while calling get_db_item_as_dict for 'before_item'")

    payload["name"] = clean_item_name(payload["name"])
    if "metatext" in payload:
        payload["metatext"] = update_metatext(payload["metatext"])

    try:
        # Fuzzy update: lets the helper map keys without hardcoding column names here
        update_result = update_db_row_by_dict(
            engine=engine,
            table=TABLE,
            uuid=item_uuid,  # required for updates; your helper should error if missing/unresolvable
            data=payload,
            fuzzy=True,
            id_col_name=ID_COL,
        )
        (
            status_code,
            is_error,
            reply_obj,
            row_data,
            primary_key,
            _message_text,
        ) = unwrap_db_result(update_result)

        if is_error:
            return jsonify(reply_obj), status_code

        log_history(item_id_1=row_data["id"], event="edited item", meta={
            "before": before_item,
            "after": row_data
        })

        if item_uuid is None:
            item_uuid = primary_key if primary_key is not None else row_data.get(ID_COL)

        if not item_uuid:
            item_uuid = payload.get(ID_COL) or item_uuid

        if not item_uuid:
            return jsonify({"error": "Save succeeded but item ID could not be determined"}), 500

        db_row = get_db_item_as_dict(engine, TABLE, item_uuid, id_col_name=ID_COL)
        if not db_row:
            return jsonify({"error": "Saved item not found"}), 404

        try:
            update_embeddings_for_item(db_row)
        except Exception:
            log.exception("Failed to refresh item embeddings after save")

        return jsonify(augment_item_dict(db_row)), 200

    except Exception as e:
        log.exception("saveitem failed")
        return jsonify({"error": str(e)}), 400


def insert_item(
    payload: Mapping[str, Any],
    *,
    engine: Optional[Engine] = None,
    include_pinned_invoices: bool = True,
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

    if data.get("is_collection") is True or data.get("is_tree_root") is True:
        # Ensure special groupings (collections and tree roots) always count as containers
        data["is_container"] = True

    # remove null creation date so the DB fills it in with now() automatically
    DEFAULTABLE_COLUMNS = {"date_creation", "date_last_modified", "textsearch"}  # add others as needed
    for col in list(data):
        if col in DEFAULTABLE_COLUMNS:
            data.pop(col)

    data["name"] = clean_item_name(data["name"])
    data["metatext"] = update_metatext(data["metatext"])

    result = update_db_row_by_dict(
        engine=engine,
        table=TABLE,
        uuid="new",
        data=data,
        fuzzy=True,
        id_col_name=ID_COL,
    )

    (
        status_code,
        is_error,
        reply_obj,
        row_data,
        primary_key,
        message_text,
    ) = unwrap_db_result(result)

    if is_error:
        raise RuntimeError(message_text)

    new_id = primary_key or row_data.get(ID_COL) or data.get(ID_COL)
    if not new_id:
        raise RuntimeError(
            "Insert succeeded but new item ID could not be determined"
        )

    db_row = get_db_item_as_dict(engine, TABLE, new_id, id_col_name=ID_COL)
    if not db_row:
        raise LookupError("Inserted item not found")

    log_history(item_id_1=db_row["id"], event="inserted item", meta=db_row)

    try:
        update_embeddings_for_item(db_row)
    except Exception:
        log.exception("Failed to refresh item embeddings after insert")

    try:
        _synchronize_pinned_relationships(
            engine,
            source_item_id=str(db_row.get(ID_COL)),
            include_invoices=include_pinned_invoices,
        )
    except Exception:
        log.exception("Failed to synchronize pinned relationships for new item")

    return augment_item_dict(db_row)


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


def _autogen_items_task(context: Dict[str, Any]) -> Dict[str, Any]:
    payload = context.get("payload") if isinstance(context, dict) else None
    if not isinstance(payload, Mapping):
        raise ValueError("Request payload must be an object.")

    invoice_value = (
        payload.get("invoice_uuid")
        or payload.get("invoiceId")
        or payload.get("invoice_id")
    )
    if not invoice_value:
        raise ValueError("Missing invoice UUID.")
    try:
        invoice_uuid = normalize_pg_uuid(str(invoice_value))
    except Exception as exc:
        raise ValueError(f"Invalid invoice UUID: {exc}")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) == 0:
        raise ValueError("Items payload must be a non-empty list.")

    engine = get_engine()
    succeeded_ids: List[str] = []
    failures: List[Dict[str, str]] = []

    for entry in raw_items:
        client_id = ""
        display_value = ""
        image_text = ""
        name_text = ""
        url_text = ""
        try:
            if not isinstance(entry, Mapping):
                raise TypeError("Each entry must be an object.")
            raw_client_id = entry.get("client_id")
            if isinstance(raw_client_id, str) and raw_client_id.strip():
                client_id = raw_client_id.strip()
            elif raw_client_id is not None:
                client_id = str(raw_client_id)

            raw_text_value = entry.get("text")
            if isinstance(raw_text_value, str):
                text_block = raw_text_value.strip()
            elif raw_text_value is not None:
                text_block = str(raw_text_value).strip()
            else:
                text_block = ""

            if not text_block:
                raise ValueError("Missing tagged text for auto-generated item.")

            structured = parse_tagged_text_to_dict(text_block,
                                                   acceptable_keys=[
                    "name",
                    "description",
                    "remarks",
                    "quantity",
                    "metatext",
                    "product_code",
                    "url",
                    "source",
                    "img_url",
                ])

            name_text = structured.get("name", "").strip()
            if name_text:
                name_text = name_text.replace("\r\n", "\n").replace("\r", "\n")
                if "\n" in name_text:
                    name_text = name_text.split("\n", 1)[0].strip()
            url_text = structured.get("url", "").strip()
            description_text = structured.get("description", "").strip()
            notes_text = structured.get("remarks", "").strip() or structured.get("notes", "").strip()

            image_value = entry.get("image")
            image_text = image_value.strip() if isinstance(image_value, str) else ""

            display_value = name_text or url_text or client_id or "(unnamed entry)"

            # The upstream parser is responsible for guaranteeing that a name exists, so we store
            # the provided value directly without inventing fallbacks here. This keeps the automatic
            # summary predictable and easy to audit.
            row_payload: Dict[str, Any] = {
                "name": name_text,
                "is_staging": True,
            }

            if url_text:
                row_payload["url"] = url_text
            if description_text:
                row_payload["description"] = description_text

            # Collect any unrecognized fields so the operator can decide how to handle them.
            # By avoiding automatic passthrough we keep the generated payload intentionally small and predictable.
            extra_notes: List[str] = []
            if notes_text:
                extra_notes.append(notes_text)

            for key, value in structured.items():
                normalized_key = str(key or "").strip()
                if not normalized_key:
                    continue
                lowered = normalized_key.lower()
                if lowered in {"name", "url", "notes", "remarks", "description"}:
                    continue
                value_text = "" if value is None else str(value)
                clean_value = value_text.strip()
                if not clean_value:
                    continue
                extra_notes.append(f"{normalized_key}: {clean_value}")

            combined_remarks = "\n".join(extra_notes).strip()

            if combined_remarks:
                row_payload["remarks"] = combined_remarks
            else:
                row_payload["remarks"] = "automatically generated from invoice"

            inserted_item = insert_item(
                row_payload,
                engine=engine,
                include_pinned_invoices=False,
            )

            # Keep embeddings fresh for items created via the background job.
            try:
                update_embeddings_for_item(inserted_item)
            except Exception:
                log.exception(
                    "Failed to refresh item embeddings during auto-generation"
                )

            # Mirror pinned item relationships without touching invoices for this flow.
            try:
                _synchronize_pinned_relationships(
                    engine,
                    source_item_id=str(inserted_item.get(ID_COL)),
                    include_invoices=False,
                )
            except Exception:
                log.exception(
                    "Failed to synchronize pinned relationships for auto-generated item"
                )

            new_item_id_value = inserted_item.get(ID_COL) if isinstance(inserted_item, Mapping) else None
            new_item_id: Optional[str] = str(new_item_id_value) if new_item_id_value else None
            if not new_item_id:
                raise RuntimeError("Insert succeeded but item ID could not be determined")

            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO invoice_items (item_id, invoice_id) VALUES (:item_id, :invoice_id)"
                    ),
                    {"item_id": new_item_id, "invoice_id": invoice_uuid},
                )

            item_uuid_obj = uuid.UUID(new_item_id)
            if image_text:
                image_error: Optional[str] = None
                try:
                    if image_text.lower().startswith("data:"):
                        store_image_for_item(
                            item_uuid=item_uuid_obj,
                            data_url=image_text,
                            clipboard_upload=True,
                        )
                    else:
                        store_image_for_item(
                            item_uuid=item_uuid_obj,
                            source_url=image_text,
                        )
                except (ImageBadRequest, ImageUnsupportedMedia) as img_exc:
                    image_error = str(img_exc)
                    log.warning("Image handling rejected for %s: %s", display_value, img_exc)
                except (RuntimeError, FileNotFoundError, ValueError) as img_exc:
                    image_error = str(img_exc)
                    log.warning("Image handling failed for %s: %s", display_value, img_exc)
                except Exception as img_exc:
                    image_error = str(img_exc)
                    log.exception("Unexpected image handling failure for %s", display_value)

                if image_error:
                    failures.append(
                        {
                            "client_id": client_id,
                            "display": f"{display_value} (image)",
                            "error": f"Image processing failed: {image_error}",
                        }
                    )

            tagged_img_url = structured.get("img_url", "")
            if tagged_img_url:
                image_error: Optional[str] = None
                try:
                    store_image_for_item(
                            item_uuid=item_uuid_obj,
                            source_url=tagged_img_url,
                        )
                except (ImageBadRequest, ImageUnsupportedMedia) as img_exc:
                    image_error = str(img_exc)
                    log.warning("Image handling rejected for %s: %s", display_value, img_exc)
                except (RuntimeError, FileNotFoundError, ValueError) as img_exc:
                    image_error = str(img_exc)
                    log.warning("Image handling failed for %s: %s", display_value, img_exc)
                except Exception as img_exc:
                    image_error = str(img_exc)
                    log.exception("Unexpected image handling failure for %s", display_value)

                if image_error:
                    failures.append(
                        {
                            "client_id": client_id,
                            "display": f"{display_value} (image)",
                            "error": f"Image processing failed: {image_error}",
                        }
                    )

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

    return response_payload


@bp.route("/autogenitems", methods=["POST"])
@login_required
def autogen_items_api():
    payload = request.get_json(silent=True) or {}
    try:
        manager = get_job_manager(current_app)
        job_id = manager.start_job(_autogen_items_task, {"payload": payload})
    except Exception as exc:
        log.exception("Failed to enqueue auto-generated item job")
        return jsonify({"success": False, "error": str(exc)}), 503

    return jsonify({"job_id": job_id})


def delete_item_relationship(relationship_identifier: Any) -> Optional[Dict[str, Any]]:
    """Remove a relationship row identified by its primary key."""

    try:
        normalized_relationship_id = normalize_pg_uuid(str(relationship_identifier))
    except Exception as exc:
        log.debug(
            "Unable to normalize relationship identifier %r for deletion: %s",
            relationship_identifier,
            exc,
        )
        return None

    engine = get_engine()

    before_row = None
    try:
        before_row = get_db_item_as_dict(engine, 'relationships', normalized_relationship_id, 'id')
    except Exception:
        log.exception("for history logging, while calling get_db_item_as_dict for 'before_row'")

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id, item_id, assoc_id, assoc_type
                FROM relationships
                WHERE id = :relationship_id
                """
            ),
            {"relationship_id": normalized_relationship_id},
        ).mappings().first()

        if existing is None:
            return None

        relationship_dict = dict(existing)

        # TODO: Surface relationship_dict to an auditing or notification pipeline so the caller can react.

        conn.execute(
            text(
                """
                DELETE FROM relationships
                WHERE id = :relationship_id
                """
            ),
            {"relationship_id": normalized_relationship_id},
        )

        log_history(
            item_id_1=before_row["item_id"] if before_row else None,
            item_id_2=before_row["assoc_id"] if before_row else None,
            event="delete relationship",
            meta=before_row,
        )

        return relationship_dict


@bp.route("/containmentpaths", methods=["GET"])
@login_required
def containment_paths_api():
    """Return every containment path leading away from the requested item."""

    target_uuid = (
        request.args.get("target_uuid")
        or request.args.get("item_uuid")
        or request.args.get("id")
    )
    if not target_uuid:
        return jsonify({"ok": False, "error": "Missing target UUID."}), 400

    try:
        normalized_target = normalize_pg_uuid(str(target_uuid))
    except Exception as exc:
        log.debug("containment_paths_api: invalid target UUID %r: %s", target_uuid, exc)
        return jsonify({"ok": False, "error": "Invalid target UUID."}), 400

    try:
        paths = fetch_containment_paths(normalized_target)
    except Exception as exc:
        log.exception("Failed to compute containment paths for %s", normalized_target)
        return jsonify({"ok": False, "error": "Unable to compute containment paths."}), 500

    return jsonify({"ok": True, "paths": paths, "target": normalized_target})


@bp.route("/bulkassoc", methods=["POST"])
@login_required
def bulk_associate_items_api():
    payload = request.get_json(silent=True) or {}
    table_name = str(payload.get("table") or "items").strip().lower()
    if table_name != "items":
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Only item associations are supported by this endpoint.",
                }
            ),
            400,
        )

    target_uuid = payload.get("target_uuid")
    if not target_uuid:
        return jsonify({"ok": False, "error": "Missing target UUID."}), 400

    try:
        normalized_target = normalize_pg_uuid(str(target_uuid))
    except Exception as exc:
        log.debug("bulk_associate_items_api: invalid target UUID %r: %s", target_uuid, exc)
        return jsonify({"ok": False, "error": "Invalid target UUID."}), 400

    raw_ids = payload.get("pks")
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "pks must be a list."}), 400

    try:
        assoc_bits = int(payload.get("association_type", 0))
    except (TypeError, ValueError):
        assoc_bits = 0

    processed: List[str] = []
    for candidate in raw_ids:
        try:
            normalized_candidate = normalize_pg_uuid(str(candidate))
        except Exception as exc:
            log.debug(
                "bulk_associate_items_api: skipping invalid item identifier %r: %s",
                candidate,
                exc,
            )
            continue

        result = set_item_relationship(normalized_candidate, normalized_target, assoc_bits)
        if result is None:
            continue
        processed.append(str(result.get("id") or normalized_candidate))

    return jsonify({"ok": True, "updated": len(processed), "relationships": processed})


@bp.route("/bulkassoc", methods=["DELETE"])
@login_required
def bulk_remove_item_associations_api():
    payload = request.get_json(silent=True) or {}
    table_name = str(payload.get("table") or "items").strip().lower()
    if table_name != "items":
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Only item associations are supported by this endpoint.",
                }
            ),
            400,
        )

    target_uuid = payload.get("target_uuid")
    if not target_uuid:
        return jsonify({"ok": False, "error": "Missing target UUID."}), 400

    try:
        normalized_target = normalize_pg_uuid(str(target_uuid))
    except Exception as exc:
        log.debug("bulk_remove_item_associations_api: invalid target UUID %r: %s", target_uuid, exc)
        return jsonify({"ok": False, "error": "Invalid target UUID."}), 400

    raw_ids = payload.get("pks")
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "pks must be a list."}), 400

    removed: List[str] = []
    for candidate in raw_ids:
        try:
            normalized_candidate = normalize_pg_uuid(str(candidate))
        except Exception as exc:
            log.debug(
                "bulk_remove_item_associations_api: skipping invalid item identifier %r: %s",
                candidate,
                exc,
            )
            continue

        relationship = get_item_relationship(normalized_candidate, normalized_target)
        if not relationship:
            continue

        deleted = delete_item_relationship(relationship.get("id"))
        if deleted is None:
            continue
        removed.append(str(relationship.get("id")))

    return jsonify({"ok": True, "removed": len(removed), "relationships": removed})


@bp.route("/moveitem", methods=["POST"])
@login_required
def move_item_api():
    """Move an item into a different container using the helper that keeps history tidy."""

    payload = request.get_json(silent=True) or {}
    item_uuid = payload.get("item_uuid")
    target_uuid = payload.get("target_uuid")

    if not item_uuid or target_uuid is None:
        return jsonify({"ok": False, "error": "Both item_uuid and target_uuid are required."}), 400

    try:
        normalized_item = normalize_pg_uuid(str(item_uuid))
    except Exception as exc:
        log.debug("move_item_api: invalid item identifier %r: %s", item_uuid, exc)
        return jsonify({"ok": False, "error": "Invalid item UUID."}), 400

    normalized_target: Optional[str] = None
    target_is_pinned = isinstance(target_uuid, str) and target_uuid.strip().lower() == "pinned"

    if target_is_pinned:
        # Special handling: treat the literal "pinned" keyword as a request to move the
        # item into the most recently pinned inventory record.
        try:
            session_handle = get_or_create_session()
        except Exception:
            log.exception("move_item_api: unable to obtain database session for pinned lookup")
            return jsonify({"ok": False, "error": "Unable to access pinned items."}), 500

        try:
            from .search import append_pinned_items  # Local import avoids circular dependency.

            pinned_rows: List[Dict[str, Any]] = []
            append_pinned_items(
                session_handle,
                "items",
                pinned_rows,
                augment_row=augment_item_dict,
            )
        except Exception:
            log.exception("move_item_api: failed to collect pinned items")
            return jsonify({"ok": False, "error": "Unable to resolve pinned target."}), 500

        if not pinned_rows:
            return jsonify({"ok": False, "error": "No pinned items are currently available."}), 400

        pinned_identifier = pinned_rows[0].get("id") or pinned_rows[0].get("pk")
        if not pinned_identifier:
            log.error("move_item_api: pinned target row missing identifier: %r", pinned_rows[0])
            return jsonify({"ok": False, "error": "Pinned target is missing an identifier."}), 500

        try:
            normalized_target = normalize_pg_uuid(str(pinned_identifier))
        except Exception as exc:
            log.exception("move_item_api: invalid pinned target identifier %r", pinned_identifier)
            return jsonify({"ok": False, "error": "Pinned target identifier is invalid."}), 500
    else:
        try:
            normalized_target = normalize_pg_uuid(str(target_uuid))
        except Exception as exc:
            log.debug(
                "move_item_api: invalid target identifier %r: %s",
                target_uuid,
                exc,
            )
            return jsonify({"ok": False, "error": "Invalid target UUID."}), 400

    if normalized_item == normalized_target:
        return jsonify({"ok": False, "error": "Item and target UUID must be different."}), 400

    try:
        result = move_item(normalized_item, normalized_target)
    except Exception:
        log.exception("move_item_api: move_item helper failed for %s -> %s", normalized_item, normalized_target)
        return jsonify({"ok": False, "error": "Move operation failed unexpectedly."}), 500

    if not isinstance(result, dict):
        return jsonify({"ok": False, "error": "Move operation returned an unexpected payload."}), 500

    if not result.get("ok"):
        error_message = str(result.get("error") or "Unable to move the item.")
        return jsonify({"ok": False, "error": error_message}), 400

    response_payload = {"ok": True, "result": result}
    return jsonify(response_payload)


@bp.route("/bulkdelete", methods=["POST"])
@login_required
def bulk_delete_items_api():
    payload = request.get_json(silent=True) or {}
    table_name = str(payload.get("table") or "items").strip().lower()
    if table_name != "items":
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Only inventory items can be deleted through this endpoint.",
                }
            ),
            400,
        )

    raw_ids = payload.get("pks")
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "pks must be a list."}), 400

    normalized_ids: List[str] = []
    for candidate in raw_ids:
        try:
            normalized_ids.append(normalize_pg_uuid(str(candidate)))
        except Exception as exc:
            log.debug(
                "bulk_delete_items_api: skipping invalid item identifier %r: %s",
                candidate,
                exc,
            )

    if not normalized_ids:
        return jsonify({"ok": False, "error": "No valid item identifiers supplied."}), 400

    delete_sql = text(
        """
        UPDATE items
        SET is_deleted = TRUE
        WHERE id IN :item_ids
        """
    ).bindparams(bindparam("item_ids", expanding=True))

    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(delete_sql, {"item_ids": normalized_ids})

        for i in normalized_ids:
            log_history(
                item_id_1=i,
                item_id_2=None,
                event="marked as deleted",
                meta=get_db_item_as_dict(engine, 'items', i),
            )

    return jsonify(
        {
            "ok": True,
            "deleted": int(result.rowcount or 0),
            "processed_ids": normalized_ids,
        }
    )
