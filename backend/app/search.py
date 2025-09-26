# backend/app/search.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

import logging
import uuid

from flask import Blueprint, jsonify, request, current_app

from .user_login import login_required
from .db import deduplicate_rows, get_or_create_session
from .search_expression import SearchQuery, get_sql_order_and_limit
from .embeddings import search_items_by_embeddings
from .helpers import fuzzy_levenshtein_at_most
from .items import augment_item_dict
from .slugify import slugify
from .static_server import get_public_html_path

from sqlalchemy import bindparam, text

from app.config_loader import get_pin_open_expiry_hours

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 50


def _pin_open_window_hours() -> int:
    """Return the configured pin window in hours, consulting Flask config when available."""
    try:
        cfg = current_app.config
    except RuntimeError:
        return get_pin_open_expiry_hours()
    return get_pin_open_expiry_hours(cfg)


def _has_directive(search_query: Any, directive_name: str) -> bool:
    """Return True when the search query declares the requested directive."""
    if not directive_name:
        return False
    directive_key = str(directive_name).strip().lower()
    if not directive_key:
        return False
    query_object = search_query
    directive_units = getattr(query_object, "directive_units", [])
    for directive in directive_units:
        # Each directive validates itself so we never rely on malformed tokens.
        ensure_valid = getattr(directive, "ensure_valid", None)
        if callable(ensure_valid):
            try:
                if not ensure_valid():
                    continue
            except Exception:
                log.debug("_has_directive: directive validation raised", exc_info=True)
                continue
        lhs_value = getattr(directive, "lhs", None)
        if isinstance(lhs_value, str) and lhs_value.strip().lower() == directive_key:
            return True
    return False


def append_pinned_items(
    session: Any,
    table_name: str,
    destination: List[Dict[str, Any]],
    *,
    augment_row: Optional[Any] = None,
) -> None:
    """Fetch and append rows that remain pinned within the configured window."""
    valid_tables = {"items", "invoices"}
    if table_name not in valid_tables:
        raise ValueError("append_pinned_items only supports 'items' or 'invoices'")
    threshold = datetime.now(timezone.utc) - timedelta(hours=_pin_open_window_hours())
    # Pull every row that remains pinned; this intentionally ignores any additional filters.
    sql = text(
        f"""
        SELECT
            *
        FROM {table_name}
        WHERE pin_as_opened IS NOT NULL
          AND pin_as_opened >= :threshold
          AND NOT is_deleted
        """
    )
    pinned_rows = session.execute(sql, {"threshold": threshold}).mappings().all()
    for row in pinned_rows:
        row_dict = dict(row)
        if callable(augment_row):
            # Allow callers to decorate the row so it matches existing result formatting.
            row_dict = augment_row(row_dict)
        destination.append(row_dict)


def _count_open_pins(session: Any, table_name: str) -> int:
    """Count rows in the requested table whose pins remain within the open window."""
    if table_name not in {"items", "invoices"}:
        raise ValueError("table_name must be either 'items' or 'invoices'")

    threshold = datetime.now(timezone.utc) - timedelta(hours=_pin_open_window_hours())

    # Use explicit SQL so the logic remains transparent and easy to audit.
    sql = text(
        f"""
        SELECT COUNT(*) AS opened_count
        FROM {table_name}
        WHERE pin_as_opened IS NOT NULL
          AND pin_as_opened >= :threshold
          AND NOT is_deleted
        """
    )

    result = session.execute(sql, {"threshold": threshold})
    count_value = result.scalar() or 0
    return int(count_value)

# Expose this blueprint from your app factory / main to register:
#   from app.search import bp as search_bp
#   app.register_blueprint(search_bp)
bp = Blueprint("search", __name__, url_prefix="/api")


def _normalize_primary_key_column(primary_key_column: str) -> str:
    if not isinstance(primary_key_column, str):
        raise TypeError("primary_key_column must be provided as a string")

    column = primary_key_column.strip()
    if not column:
        raise ValueError("primary_key_column cannot be empty")

    if not column.isidentifier():
        raise ValueError("primary_key_column must be a valid identifier string")

    return column


def _unsigned_to_signed_32(value: int) -> int:
    masked = value & 0xFFFFFFFF
    if masked >= 0x80000000:
        return masked - 0x100000000
    return masked


def _short_id_candidates(identifier: str) -> List[int]:
    token = (identifier or "").strip()
    if not token:
        return []

    candidates: List[int] = []

    try:
        hex_value = int(token, 16)
    except ValueError:
        hex_value = None

    if hex_value is not None:
        candidates.append(_unsigned_to_signed_32(hex_value))

    digit_token = token.lstrip("+-")
    if digit_token.isdigit():
        try:
            decimal_value = int(token, 10)
        except ValueError:
            decimal_value = None
        else:
            decimal_value = _unsigned_to_signed_32(decimal_value)
            if decimal_value not in candidates:
                candidates.append(decimal_value)

    return candidates


def _pick_best_short_id_row(
    rows: List[Mapping[str, Any]],
    comparison_text: str,
) -> Mapping[str, Any]:
    if len(rows) == 1:
        return rows[0]

    comparison_text_norm = comparison_text.lower()

    def _distance(row: Mapping[str, Any]) -> tuple[int, str]:
        name = str(row.get("name") or "")
        name_norm = name.lower()
        limit = max(len(comparison_text_norm), len(name_norm), 1)
        dist = fuzzy_levenshtein_at_most(
            comparison_text_norm,
            name_norm,
            limit=limit,
        )
        return (dist, name_norm)

    return min(rows, key=_distance)


def _execute_text_search_query(
    session: Any,
    search_query: SearchQuery,
    query_text: str,
    *,
    default_table: str,
    default_alias: str,
    select_template: Optional[str] = None,
    textsearch_template: Optional[str] = None,
    default_order_templates: Optional[Iterable[str]] = None,
    default_limit: int = DEFAULT_LIMIT,
) -> List[Mapping[str, Any]]:
    """Execute the dynamic SQL generated for a :class:`SearchQuery`.

    Parameters mirror the knobs used by :func:`search_items` and the new
    :func:`search_invoices` function so that the fairly involved SQL building
    only lives in a single place.
    """

    smart_directive = False
    if isinstance(search_query, SearchQuery):
        for directive in getattr(search_query, "directive_units", []):
            lhs_value = getattr(directive, "lhs", None)
            ensure_valid = getattr(directive, "ensure_valid", None)
            if callable(ensure_valid):
                try:
                    if not ensure_valid():
                        continue
                except Exception:
                    log.debug("Directive validation failed", exc_info=True)
                    continue
            if isinstance(lhs_value, str) and lhs_value.lower() == "smart":
                smart_directive = True
                break
    if smart_directive and (query_text or "").strip() != "*":
        return search_items_by_embeddings(search_query, session=session, limit=default_limit)

    criteria = search_query.get_sql_conditionals()

    table_name = criteria.get("table", default_table)
    alias = criteria.get("table_alias") or default_alias

    select_clause = (select_template or "{alias}.*").format(alias=alias)
    textsearch_expr = (textsearch_template or "{alias}.textsearch").format(alias=alias)

    normalized_query = (query_text or "").strip()
    use_textsearch = bool(normalized_query and normalized_query != "*")

    ts_query_expr = None
    rank_expression = None
    if use_textsearch:
        ts_query_expr = "websearch_to_tsquery('english', :q)"
        rank_expression = f"ts_rank_cd({textsearch_expr}, {ts_query_expr})"

    where_clauses: List[str] = []
    touched_columns = criteria.get("touched_columns") or set()
    if "is_deleted" not in touched_columns:
        where_clauses.append(f"NOT {alias}.is_deleted")
    if use_textsearch and ts_query_expr:
        where_clauses.append(f"{textsearch_expr} @@ {ts_query_expr}")
    for condition in criteria.get("where", []):
        if condition:
            where_clauses.append(condition)

    order_by_clauses, limit_value, offset_value = get_sql_order_and_limit(
        criteria,
        alias=alias,
        use_textsearch=use_textsearch,
        rank_expression=rank_expression,
        default_order_templates=default_order_templates,
        default_limit=default_limit,
    )

    sql_lines = [
        "SELECT",
        f"    {select_clause}",
        f"FROM {table_name} AS {alias}",
    ]
    if where_clauses:
        sql_lines.append("WHERE")
        sql_lines.append(f"    {where_clauses[0]}")
        for condition in where_clauses[1:]:
            sql_lines.append(f"    AND {condition}")
    if order_by_clauses:
        sql_lines.append("ORDER BY")
        for idx, clause in enumerate(order_by_clauses):
            prefix = "    " if idx == 0 else "    , "
            sql_lines.append(f"{prefix}{clause}")
    if limit_value is not None:
        sql_lines.append("LIMIT :limit")
    if offset_value:
        sql_lines.append("OFFSET :offset")

    base_sql = text("\n".join(sql_lines))

    sql_params: Dict[str, Any] = {}
    if use_textsearch:
        sql_params["q"] = normalized_query or query_text
    sql_params.update(criteria.get("params", {}))
    if limit_value is not None:
        sql_params["limit"] = limit_value
    if offset_value:
        sql_params["offset"] = offset_value

    return session.execute(base_sql, sql_params).mappings().all()


def _finalize_item_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize search results with required metadata.

    Each row is copied so that the original mappings returned by SQLAlchemy
    remain untouched.  The helper enforces the following invariants:

    * ``id`` values are coerced to ``str``
    * ``pk`` mirrors the ``id`` value (when present)
    * ``slug`` is regenerated using :func:`backend.app.slugify.slugify`
    * duplicates are removed via :func:`deduplicate_rows` using ``pk``
    """

    normalized: List[Dict[str, Any]] = []
    for row in rows:
        row_dict: Dict[str, Any] = dict(row)

        identifier = row_dict.get("id")
        if identifier is None and row_dict.get("pk") is not None:
            identifier = row_dict.get("pk")

        if identifier is not None:
            identifier_str = str(identifier)
            row_dict["id"] = identifier_str
            row_dict["pk"] = identifier_str
        else:
            row_dict.pop("pk", None)

        row_dict["slug"] = slugify(
            row_dict.get("name"),
            row_dict.get("short_id"),
        )

        normalized.append(row_dict)

    if not normalized:
        return normalized

    return deduplicate_rows(normalized, key="pk")


def _finalize_invoice_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        row_dict: Dict[str, Any] = dict(row)

        identifier = row_dict.get("id")
        if identifier is None and row_dict.get("pk") is not None:
            identifier = row_dict.get("pk")

        if identifier is not None:
            identifier_str = str(identifier)
            row_dict["id"] = identifier_str
            row_dict["pk"] = identifier_str
        else:
            row_dict.pop("pk", None)

        normalized.append(row_dict)

    if not normalized:
        return normalized

    return deduplicate_rows(normalized, key="pk")


def _to_bool(value: Any) -> bool:
    """Convert loose truthy/falsey values to :class:`bool`."""

    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _build_thumbnail_public_url(dir_value: Any, file_name: Any) -> Optional[str]:
    """Return a browser-accessible thumbnail path for an image row."""

    raw_name = str(file_name or "").strip()
    if not raw_name:
        return None

    raw_dir = str(dir_value or "").strip()
    safe_dir = raw_dir.strip("/\")
    safe_name = raw_name.lstrip("/\")

    def _split_segments(value: str) -> list[str]:
        """Normalize a path-like value into safe URL segments."""
        sanitized = value.replace("\", "/")
        return [segment for segment in sanitized.split("/") if segment]

    dir_segments = _split_segments(safe_dir)
    name_segments = _split_segments(safe_name)
    if not name_segments:
        return None

    base_path = get_public_html_path()

    def _build_path(segments: list[str]) -> Any:
        """Construct an absolute path beneath the public HTML root."""
        current = base_path
        for part in segments:
            current = current / part
        return current

    base_segments = ["imgs"] + dir_segments + name_segments
    selected_segments = list(base_segments)
    selected_path = _build_path(selected_segments)

    # Prefer a dedicated thumbnail when it lives beside the original image file.
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


def _fetch_item_thumbnail_map(
    session: Any, item_ids: Iterable[Any]
) -> Dict[str, str]:
    """Fetch thumbnail URLs for the provided item identifiers."""

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

    rows = session.execute(thumb_sql, {"item_ids": unique_ids}).mappings().all()

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


def search_items(
    raw_query: str,
    target_uuid: Optional[str] = None,
    context: Any = None,
    primary_key_column: str = "id",
) -> List[Dict[str, Any]]:
    """
    Execute an item search.

    Parameters
    ----------
    raw_query : str
        The raw query string (as typed by the user).
    target_uuid : Optional[str]
        If provided, future enhancement: bias/filter results related to this item.
        NOTE: The base text search always runs regardless.
    context : Any
        Optional execution context; may contain request/session info later.
    primary_key_column : str
        Column name to use when resolving direct UUID lookups. Defaults to "id".

    Returns
    -------
    List[Dict[str, Any]]
        Deduplicated item dictionaries ready for JSON serialization.  Each
        entry includes a ``pk`` (mirroring ``id``) and a ``slug`` computed via
        :func:`backend.app.slugify.slugify`.
    """
    session = get_or_create_session()
    results: List[Dict[str, Any]] = []

    if not (raw_query and raw_query.strip()):
        log.info("search_items: empty query -> returning empty list")
        return _finalize_item_rows(results)

    # Parse the query and extract normalized free-text

    if isinstance(context, dict):
        sq_context = dict(context)
    else:
        sq_context = {}
        if context is not None:
            sq_context["payload"] = context
    sq_context.setdefault("table", "items")
    sq_context.setdefault("table_alias", "i")
    sq = SearchQuery(s=raw_query, context=sq_context)

    if not target_uuid and len(sq.identifiers) == 1:
        identifier = sq.identifiers[0]

        uuid_candidate: Optional[str]
        try:
            uuid_candidate = str(uuid.UUID(identifier))
        except (ValueError, AttributeError, TypeError):
            uuid_candidate = None

        if uuid_candidate and not (sq.query_text or "").strip():
            column = _normalize_primary_key_column(primary_key_column)

            direct_sql = text(
                f"""
                SELECT
                    i.*
                FROM items AS i
                WHERE
                    NOT i.is_deleted
                    AND i.{column} = :identifier
                LIMIT 1
                """
            )

            row = session.execute(direct_sql, {"identifier": uuid_candidate}).mappings().first()
            if row:
                return _finalize_item_rows([augment_item_dict(row)])
        else:
            short_id_values = _short_id_candidates(identifier)
            if short_id_values:
                comparison_text = (sq.query_text or "").strip() or raw_query.strip()
                short_sql = text(
                    """
                    SELECT
                        i.*
                    FROM items AS i
                    WHERE
                        NOT i.is_deleted
                        AND i.short_id = :short_id
                    """
                )

                for value in short_id_values:
                    sid_rows = session.execute(short_sql, {"short_id": value}).mappings().all()
                    if not sid_rows:
                        continue

                    if comparison_text:
                        best_row = _pick_best_short_id_row(sid_rows, comparison_text)
                        return _finalize_item_rows([augment_item_dict(best_row)])

                    return _finalize_item_rows([augment_item_dict(sid_rows[0])])

    query_text = sq.query_text or raw_query  # fallback just in case

    rows = _execute_text_search_query(
        session,
        sq,
        query_text,
        default_table="items",
        default_alias="i",
        default_order_templates=["{alias}.date_last_modified {direction}"],
    )

    for row in rows:
        row_dict = dict(row)
        if sq.evaluate(row_dict):
            results.append(augment_item_dict(row_dict))

    if _has_directive(sq, "pinned"):
        append_pinned_items(
            session,
            "items",
            results,
            augment_row=augment_item_dict,
        )

    # --- RELATION / TARGET-UUID ENHANCEMENT ---
    # This is intentionally *not* an else-if. The base search above should always take place.
    if target_uuid:
        relation_sql = text("""
        SELECT
            r.item_id,
            r.assoc_id,
            r.assoc_type
        FROM relationships AS r
        WHERE
            r.item_id = :target_uuid
            OR r.assoc_id = :target_uuid
        """)
        relation_rows = session.execute(relation_sql, {"target_uuid": target_uuid}).mappings().all()

        target_str = str(target_uuid)
        relation_map: Dict[str, int] = {}
        for relation in relation_rows:
            relation_item_id = relation.get("item_id")
            relation_assoc_id = relation.get("assoc_id")
            if relation_item_id is None or relation_assoc_id is None:
                continue

            item_id_str = str(relation_item_id)
            assoc_id_str = str(relation_assoc_id)

            if item_id_str == target_str:
                other_id = assoc_id_str
            elif assoc_id_str == target_str:
                other_id = item_id_str
            else:
                continue

            assoc_type_value = relation.get("assoc_type")
            if assoc_type_value is None:
                assoc_type_value = -1

            existing_value = relation_map.get(other_id)
            if existing_value is None:
                relation_map[other_id] = assoc_type_value
            elif existing_value < 0:
                if assoc_type_value >= 0:
                    relation_map[other_id] = assoc_type_value
            elif assoc_type_value >= 0:
                relation_map[other_id] = existing_value | assoc_type_value

        for item in results:
            pk_value = item.get("pk") or item.get("id")
            assoc_type = -1
            if pk_value is not None:
                assoc_type = relation_map.get(str(pk_value), -1)
            item["assoc_type"] = assoc_type


    return _finalize_item_rows(results)


def search_invoices(
    raw_query: str,
    target_uuid: Optional[str] = None,
    context: Any = None,
    primary_key_column: str = "id",
) -> List[Dict[str, Any]]:
    session = get_or_create_session()
    results: List[Dict[str, Any]] = []

    if not (raw_query and raw_query.strip()):
        log.info("search_invoices: empty query -> returning empty list")
        return _finalize_invoice_rows(results)

    if isinstance(context, dict):
        sq_context = dict(context)
    else:
        sq_context = {}
        if context is not None:
            sq_context["payload"] = context
    sq_context.setdefault("table", "invoices")
    sq_context.setdefault("table_alias", "inv")
    sq = SearchQuery(s=raw_query, context=sq_context)

    if not target_uuid and len(sq.identifiers) == 1:
        identifier = sq.identifiers[0]

        uuid_candidate: Optional[str]
        try:
            uuid_candidate = str(uuid.UUID(identifier))
        except (ValueError, AttributeError, TypeError):
            uuid_candidate = None

        if uuid_candidate and not (sq.query_text or "").strip():
            column = _normalize_primary_key_column(primary_key_column)

            direct_sql = text(
                f"""
                SELECT
                    inv.*
                FROM invoices AS inv
                WHERE
                    NOT inv.is_deleted
                    AND inv.{column} = :identifier
                LIMIT 1
                """
            )

            row = session.execute(direct_sql, {"identifier": uuid_candidate}).mappings().first()
            if row:
                return _finalize_invoice_rows([row])

    query_text = sq.query_text or raw_query

    textsearch_template = (
        "to_tsvector('english', "
        "COALESCE({alias}.subject, '') || ' ' || "
        "COALESCE({alias}.notes, '') || ' ' || "
        "COALESCE({alias}.order_number, '') || ' ' || "
        "COALESCE({alias}.shop_name, '') || ' ' || "
        "COALESCE({alias}.urls, '') || ' ' || "
        "COALESCE({alias}.html, ''))"
    )

    rows = _execute_text_search_query(
        session,
        sq,
        query_text,
        default_table="invoices",
        default_alias="inv",
        select_template="{alias}.*",
        textsearch_template=textsearch_template,
        default_order_templates=["{alias}.date {direction}"],
    )

    for row in rows:
        row_dict = dict(row)
        if sq.evaluate(row_dict):
            results.append(row_dict)

    if _has_directive(sq, "pinned"):
        append_pinned_items(
            session,
            "invoices",
            results,
        )

    if target_uuid and results:
        invoice_ids: List[str] = []
        seen: set[str] = set()
        for invoice in results:
            pk_value = invoice.get("pk") or invoice.get("id")
            if pk_value is None:
                continue
            value = str(pk_value)
            if value in seen:
                continue
            seen.add(value)
            invoice_ids.append(value)

        if invoice_ids:
            assoc_sql = (
                text(
                    """
                    SELECT DISTINCT ii.invoice_id
                    FROM invoice_items AS ii
                    WHERE ii.item_id = :item_id
                      AND ii.invoice_id IN :invoice_ids
                    """
                ).bindparams(bindparam("invoice_ids", expanding=True))
            )
            assoc_rows = session.execute(
                assoc_sql,
                {"item_id": target_uuid, "invoice_ids": invoice_ids},
            ).scalars().all()
            associated_ids = {str(value) for value in assoc_rows}

            for invoice in results:
                pk_value = invoice.get("pk") or invoice.get("id")
                is_associated = False
                if pk_value is not None:
                    is_associated = str(pk_value) in associated_ids
                invoice["is_associated"] = is_associated

    return _finalize_invoice_rows(results)



@bp.route("/pinsummary", methods=["GET"])
@login_required
def pin_summary_api():
    """Return how many items and invoices are still considered opened pins."""
    try:
        session = get_or_create_session()

        # Follow the shared 36-hour window to decide whether a pin is still active.
        items_opened = _count_open_pins(session, "items")
        invoices_opened = _count_open_pins(session, "invoices")

        payload = {
            "items_opened": items_opened,
            "invoices_opened": invoices_opened,
        }

        return jsonify(ok=True, data=payload)
    except Exception as exc:
        log.exception("pin_summary_api: unable to compute pin summary")
        return jsonify(ok=False, error=str(exc)), 500

@bp.route("/search", methods=["POST"])
@login_required
def search_api():
    """
    POST /api/search
    JSON body:
      {
        "q": "string",                # required
        "target_uuid": "uuid-string", # optional
        "include_thumbnails": bool     # optional; default false
      }

    Response:
      { "ok": true, "data": [...] } on success
      { "ok": false, "error": "..." } on failure
    """
    try:
        data = request.get_json(silent=True) or {}
        raw_query = (data.get("q") or "").strip()
        target_uuid = data.get("target_uuid") or None
        include_thumbnails = _to_bool(data.get("include_thumbnails"))

        # Context can include request info if you want it later
        ctx = {
            "ip": request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            # In future: you might stash the DB session or auth user here.
        }

        if not raw_query:
            return jsonify(ok=True, data=[])

        items = search_items(raw_query=raw_query, target_uuid=target_uuid, context=ctx)

        if include_thumbnails and items:
            session = get_or_create_session()
            thumbnail_map = _fetch_item_thumbnail_map(
                session,
                (item.get("pk") for item in items),
            )
            for item in items:
                pk_value = item.get("pk")
                thumbnail_url = None
                if pk_value is not None:
                    thumbnail_url = thumbnail_map.get(str(pk_value))
                item["thumbnail"] = thumbnail_url or ""

        return jsonify(ok=True, data=items)

    except Exception as e:
        log.exception("search_api: error while handling search")
        return jsonify(ok=False, error=str(e)), 400


@bp.route("/searchinvoices", methods=["POST"])
@login_required
def search_invoices_api():
    """Endpoint for invoice search requests."""

    try:
        data = request.get_json(silent=True) or {}
        raw_query = (data.get("q") or "").strip()
        target_uuid = data.get("target_uuid") or None

        ctx = {
            "ip": request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
        }

        if not raw_query:
            return jsonify(ok=True, data=[])

        invoices = search_invoices(raw_query=raw_query, target_uuid=target_uuid, context=ctx)
        return jsonify(ok=True, data=invoices)

    except Exception as e:
        log.exception("search_invoices_api: error while handling invoice search")
        return jsonify(ok=False, error=str(e)), 400
