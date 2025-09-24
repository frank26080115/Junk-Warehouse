# backend/app/search.py

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

import logging
import uuid

from flask import Blueprint, jsonify, request

from .user_login import login_required
from .db import get_or_create_session
from .search_expression import SearchQuery
from .helpers import fuzzy_levenshtein_at_most
from .items import augment_item_dict

from sqlalchemy import text

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 50

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
        A list of row dicts ready for JSON, each with added 'slug' and 'thumbnail'.
    """
    session = get_or_create_session()
    results: List[Dict[str, Any]] = []

    if not (raw_query and raw_query.strip()):
        log.info("search_items: empty query -> returning empty list")
        return results

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
                return [augment_item_dict(row)]
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
                        return [augment_item_dict(best_row)]

                    return [augment_item_dict(sid_rows[0])]

    query_text = sq.query_text or raw_query  # fallback just in case

    
    # --- BASE TEXT SEARCH (dynamic SQL generation) ---
    criteria = sq.get_sql_conditionals()
    table_name = criteria.get("table", "items")
    alias = criteria.get("table_alias") or "i"
    ts_query_expr = "websearch_to_tsquery('english', :q)"
    rank_expression = f"ts_rank_cd({alias}.textsearch, {ts_query_expr})"

    where_clauses: List[str] = []
    touched_columns = criteria.get("touched_columns") or set()
    if "is_deleted" not in touched_columns:
        where_clauses.append(f"NOT {alias}.is_deleted")
    where_clauses.append(f"{alias}.textsearch @@ {ts_query_expr}")
    for condition in criteria.get("where", []):
        if condition:
            where_clauses.append(condition)

    flags = criteria.get("flags") or {}
    order_by_clauses: List[str] = list(criteria.get("order_by") or [])
    if order_by_clauses:
        if not flags.get("random_order"):
            order_by_clauses.append(f"{rank_expression} DESC")
    else:
        base_direction = "ASC" if flags.get("reverse_default_order") else "DESC"
        order_by_clauses.append(f"{rank_expression} {base_direction}")
        order_by_clauses.append(f"{alias}.date_last_modified {base_direction}")

    limit_value = criteria.get("limit")
    limit_is_explicit = criteria.get("limit_is_explicit", False)
    page_number = criteria.get("page")
    show_all = flags.get("show_all", False)

    if not limit_is_explicit:
        limit_value = DEFAULT_LIMIT
    elif show_all:
        limit_value = None

    offset_value: Optional[int] = None
    if isinstance(page_number, int) and page_number > 1 and limit_value:
        offset_value = (page_number - 1) * limit_value

    sql_lines = [
        "SELECT",
        f"    {alias}.*",
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

    sql_params: Dict[str, Any] = {"q": query_text}
    sql_params.update(criteria.get("params", {}))
    if limit_value is not None:
        sql_params["limit"] = limit_value
    if offset_value:
        sql_params["offset"] = offset_value

    rows = session.execute(base_sql, sql_params).mappings().all()

    for row in rows:
        row_dict = dict(row)
        if sq.evaluate(row_dict):
            results.append(augment_item_dict(row_dict))

    # --- RELATION / TARGET-UUID ENHANCEMENT ---
    # This is intentionally *not* an else-if. The base search above should always take place.
    if target_uuid:
        # TODO: When a target item is provided, augment the results with:
        #   - items related to the target (parents/children/containers/contents),
        #   - or adjust ranking to prefer items proximate to the target in your graph.
        # Potential approach:
        #   1) Find related item IDs via your relation tables.
        #   2) Run a second query (or join) to fetch/merge those items.
        #   3) De-duplicate and possibly re-rank the combined results.
        log.debug("search_items: target_uuid=%s provided (TODO enhance relation-aware search)", target_uuid)
        # Example placeholder: no-op for now.

        pass

    return results


@bp.route("/search", methods=["POST"])
@login_required
def search_api():
    """
    POST /api/search
    JSON body:
      {
        "q": "string",                # required
        "target_uuid": "uuid-string"  # optional
      }

    Response:
      { "ok": true, "data": [...] } on success
      { "ok": false, "error": "..." } on failure
    """
    try:
        data = request.get_json(silent=True) or {}
        raw_query = (data.get("q") or "").strip()
        target_uuid = data.get("target_uuid") or None

        # Context can include request info if you want it later
        ctx = {
            "ip": request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            # In future: you might stash the DB session or auth user here.
        }

        if not raw_query:
            return jsonify(ok=True, data=[])

        items = search_items(raw_query=raw_query, target_uuid=target_uuid, context=ctx)
        return jsonify(ok=True, data=items)

    except Exception as e:
        log.exception("search_api: error while handling search")
        return jsonify(ok=False, error=str(e)), 400
