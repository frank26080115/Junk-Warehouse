# backend/app/search.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

import logging
import uuid

from flask import Blueprint, jsonify, request, current_app

from .user_login import login_required
from .db import (
    deduplicate_rows,
    get_db_item_as_dict,
    get_engine,
    session_scope,
)
from .search_expression import SearchQuery, get_sql_order_and_limit
from .embeddings import search_items_by_embeddings, EMB_TBL_NAME_PREFIX_ITEMS, EMB_TBL_NAME_PREFIX_CONTAINER
from .helpers import fuzzy_levenshtein_at_most, normalize_pg_uuid, split_words, to_bool
from .items import augment_item_dict, get_item_thumbnails
from .metatext import get_word_synonyms
from .slugify import slugify

from sqlalchemy import bindparam, text

from app.config_loader import get_pin_open_expiry_hours
from .assoc_helper import MERGE_BIT

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 50


def _pin_open_window_hours() -> int:
    """Return the configured pin window in hours, consulting Flask config when available."""
    try:
        cfg = current_app.config
    except RuntimeError:
        return get_pin_open_expiry_hours()
    return get_pin_open_expiry_hours(cfg)


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
        ORDER BY pin_as_opened DESC
        """
    )
    pinned_rows = session.execute(sql, {"threshold": threshold}).mappings().all()

    prepared_rows: List[Dict[str, Any]] = []
    for row in pinned_rows:
        row_dict = dict(row)
        if callable(augment_row):
            # Allow callers to decorate the row so it matches existing result formatting.
            row_dict = augment_row(row_dict)
        prepared_rows.append(row_dict)

    if not prepared_rows:
        # Nothing to merge, so leave the destination list untouched.
        return

    # Place pinned rows before the existing results, then drop duplicates so pinned versions win.
    combined_rows = prepared_rows + list(destination)
    deduped_rows = deduplicate_rows(combined_rows, key="id", keep="first")
    destination[:] = deduped_rows


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


def find_code_matched_items(
    target_uuid: Any = None,
    *,
    product_codes: Optional[Iterable[Any]] = None,
    urls: Optional[Iterable[Any]] = None,
) -> List[str]:
    """Return identifiers of items with matching product codes or URLs."""

    def _split_values(raw_value: Any) -> List[str]:
        """Split semicolon-delimited strings into a list of trimmed tokens."""

        if raw_value is None:
            return []

        if isinstance(raw_value, str):
            raw_texts = [raw_value]
        elif isinstance(raw_value, Iterable) and not isinstance(raw_value, (bytes, bytearray)):
            tokens: List[str] = []
            for element in raw_value:
                tokens.extend(_split_values(element))
            return tokens
        else:
            raw_texts = [str(raw_value)]

        parts: List[str] = []
        for raw_text in raw_texts:
            for piece in str(raw_text).split(';'):
                candidate = piece.strip()
                if candidate:
                    parts.append(candidate)
        # Preserve insertion order while removing duplicates.
        return list(dict.fromkeys(parts))

    def _append_unique(destination: List[str], values: Iterable[str]) -> None:
        seen = set(destination)
        for value in values:
            if value not in seen:
                destination.append(value)
                seen.add(value)

    candidate_codes: List[str] = []
    candidate_urls: List[str] = []

    if product_codes is not None:
        _append_unique(candidate_codes, _split_values(product_codes))
    if urls is not None:
        _append_unique(candidate_urls, _split_values(urls))

    normalized_uuid: Optional[str] = None
    target_uuid_obj: Optional[uuid.UUID] = None

    if target_uuid:
        try:
            normalized_uuid = normalize_pg_uuid(str(target_uuid))
        except Exception:
            log.warning("find_code_matched_items: unable to normalize UUID %r", target_uuid)
            return []

        try:
            target_uuid_obj = uuid.UUID(normalized_uuid)
        except (ValueError, AttributeError, TypeError):
            log.warning("find_code_matched_items: invalid UUID %r after normalization", normalized_uuid)
            return []

        engine = get_engine()

        try:
            target_item = get_db_item_as_dict(engine, "items", normalized_uuid)
        except LookupError:
            log.info("find_code_matched_items: no item found for UUID %s", normalized_uuid)
            return []
        except ValueError:
            log.warning("find_code_matched_items: invalid UUID supplied %r", normalized_uuid)
            return []

        _append_unique(candidate_codes, _split_values(target_item.get("product_code")))
        _append_unique(candidate_urls, _split_values(target_item.get("url")))

    if not candidate_codes and not candidate_urls:
        return []

    matched_ids: List[str] = []
    seen_ids: set[str] = set()

    with session_scope() as session:
        if target_uuid_obj is not None:
            product_sql = text(
                """
            SELECT id
            FROM items
            WHERE NOT is_deleted
              AND id <> :target_id
              AND product_code ILIKE :needle
            """
            )
            url_sql = text(
                """
            SELECT id
            FROM items
            WHERE NOT is_deleted
              AND id <> :target_id
              AND url ILIKE :needle
            """
            )
        else:
            product_sql = text(
                """
            SELECT id
            FROM items
            WHERE NOT is_deleted
              AND product_code ILIKE :needle
            """
            )
            url_sql = text(
                """
            SELECT id
            FROM items
            WHERE NOT is_deleted
              AND url ILIKE :needle
            """
            )

        def _record_matches(sql_statement: Any, needle: str) -> None:
            """Execute a query and merge unique identifiers into the accumulator."""

            cleaned = (needle or "").strip()
            if not cleaned:
                return

            parameters = {"needle": f"%{cleaned}%"}
            if target_uuid_obj is not None:
                parameters["target_id"] = target_uuid_obj
            rows = session.execute(sql_statement, parameters).scalars().all()
            for raw_id in rows:
                if raw_id is None:
                    continue
                identifier = str(raw_id)
                if identifier in seen_ids:
                    continue
                seen_ids.add(identifier)
                matched_ids.append(identifier)

        for code in candidate_codes:
            _record_matches(product_sql, code)

        for url_value in candidate_urls:
            _record_matches(url_sql, url_value)

    return matched_ids


def append_code_matched_items(
    destination: List[Dict[str, Any]],
    matched_ids: Iterable[Any],
    *,
    augment_row: Optional[Callable[[Mapping[str, Any]], Dict[str, Any]]] = None,
) -> None:
    """Hydrate matched item identifiers and prepend them to the destination list."""

    identifiers = [str(identifier) for identifier in matched_ids if identifier]
    if not identifiers:
        # Nothing to do when there are no candidate identifiers to hydrate.
        return

    engine = get_engine()

    existing_ids: set[str] = set()
    for item in destination:
        pk_value = item.get("pk") or item.get("id")
        if pk_value is None:
            continue
        existing_ids.add(str(pk_value))

    # Track which identifiers we have already materialized to avoid duplicates.
    seen_ids = set(existing_ids)
    hydrated_rows: List[Dict[str, Any]] = []

    formatter = augment_row or augment_item_dict

    for identifier in identifiers:
        if identifier in seen_ids:
            continue

        try:
            raw_row = get_db_item_as_dict(engine, "items", identifier)
        except LookupError:
            # The row vanished between discovery and hydration; ignore quietly.
            continue
        except ValueError:
            # Defensive guard in case an identifier cannot be coerced to UUID.
            continue

        hydrated_rows.append(formatter(raw_row))
        seen_ids.add(identifier)

    if not hydrated_rows:
        return

    destination[:0] = hydrated_rows


def _collect_merge_ready_item_ids(session: Any) -> set[str]:
    """Return identifiers for items that participate in a merge-marked relationship."""

    merge_sql = text(
        """
        SELECT DISTINCT candidate_id
        FROM (
            SELECT r.item_id AS candidate_id
            FROM relationships AS r
            WHERE (COALESCE(r.assoc_type, 0) & :merge_bit) <> 0
            UNION ALL
            SELECT r.assoc_id AS candidate_id
            FROM relationships AS r
            WHERE (COALESCE(r.assoc_type, 0) & :merge_bit) <> 0
        ) AS related_candidates
        WHERE candidate_id IS NOT NULL
        """
    )

    merge_ids = session.execute(merge_sql, {"merge_bit": MERGE_BIT}).scalars().all()
    normalized_ids: set[str] = set()
    for identifier in merge_ids:
        if not identifier:
            continue
        normalized_ids.add(str(identifier))
    return normalized_ids


def _execute_mergewaiting_inventory_query(
    session: Any,
    criteria: Mapping[str, Any],
    *,
    table_name: str,
    alias: str,
    select_clause: str,
    default_order_templates: Optional[Iterable[str]],
    default_limit: int,
) -> List[Mapping[str, Any]]:
    """Execute a merge-aware listing that respects existing filters and limits."""

    where_clauses: List[str] = []
    touched_columns = criteria.get("touched_columns") or set()
    if "is_deleted" not in touched_columns:
        where_clauses.append(f"NOT {alias}.is_deleted")
    for condition in criteria.get("where", []):
        if condition:
            where_clauses.append(condition)

    order_by_clauses, limit_value, offset_value = get_sql_order_and_limit(
        criteria,
        alias=alias,
        use_textsearch=False,
        rank_expression=None,
        default_order_templates=default_order_templates,
        default_limit=default_limit,
    )

    sql_lines = [
        "SELECT",
        f"    DISTINCT {select_clause}",
        f"FROM {table_name} AS {alias}",
        "JOIN relationships AS rel",
        f"    ON (rel.item_id = {alias}.id OR rel.assoc_id = {alias}.id)",
        "WHERE",
        "    (COALESCE(rel.assoc_type, 0) & :merge_bit) <> 0",
    ]

    for condition in where_clauses:
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

    sql_params: Dict[str, Any] = {"merge_bit": MERGE_BIT}
    sql_params.update(criteria.get("params", {}))
    if limit_value is not None:
        sql_params["limit"] = limit_value
    if offset_value:
        sql_params["offset"] = offset_value

    merge_sql = text("\n".join(sql_lines))
    return session.execute(merge_sql, sql_params).mappings().all()


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
    suggest_directive = False
    mergewaiting_directive = False
    normalized_query = (query_text or "").strip()
    if isinstance(search_query, SearchQuery):
        # Delegate to SearchQuery.has_directive so validation remains consistent
        # and so this function does not need to inspect directive internals.
        smart_directive = search_query.has_directive("smart")
        suggest_directive = search_query.has_directive("suggest")
        mergewaiting_directive = search_query.has_directive("mergewaiting")
    if suggest_directive and normalized_query != "*":
        return search_items_by_embeddings(
            search_query,
            session=session,
            limit=default_limit,
            embedding_table=EMB_TBL_NAME_PREFIX_CONTAINER,
        )
    if smart_directive and normalized_query != "*":
        return search_items_by_embeddings(search_query, session=session, limit=default_limit)

    criteria = search_query.get_sql_conditionals()

    table_name = criteria.get("table", default_table)
    alias = criteria.get("table_alias") or default_alias

    select_clause = (select_template or "{alias}.*").format(alias=alias)
    textsearch_expr = (textsearch_template or "{alias}.textsearch").format(alias=alias)

    if (
        mergewaiting_directive
        and table_name == default_table
        and table_name == "items"
        and normalized_query in {"", "*"}
    ):
        # When the mergewaiting directive is paired with an empty or wildcard
        # query we gather every merge-ready item while still honouring the
        # caller's filter configuration.
        return _execute_mergewaiting_inventory_query(
            session,
            criteria,
            table_name=table_name,
            alias=alias,
            select_clause=select_clause,
            default_order_templates=default_order_templates,
            default_limit=default_limit,
        )

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


def _execute_metatext_search(
    session: Any,
    search_query: SearchQuery,
    query_text: str,
    *,
    default_table: str,
    default_alias: str,
    select_template: Optional[str] = None,
    default_order_templates: Optional[Iterable[str]] = None,
    default_limit: int = DEFAULT_LIMIT,
) -> List[Mapping[str, Any]]:
    """Execute a metatext search that requires synonym matches for every word."""

    # Prepare a normalized set of words while preserving their original intent.
    normalized_query = (query_text or "").strip()
    words = split_words(normalized_query)
    if not words:
        # Without any searchable words the metatext clause would become meaningless.
        return []

    criteria = search_query.get_sql_conditionals()
    table_name = criteria.get("table", default_table)
    alias = criteria.get("table_alias") or default_alias
    select_clause = (select_template or "{alias}.*").format(alias=alias)

    where_clauses: List[str] = []
    touched_columns = criteria.get("touched_columns") or set()
    if "is_deleted" not in touched_columns:
        # Ensure soft-deleted rows stay hidden unless an upstream filter already did so.
        where_clauses.append(f"NOT {alias}.is_deleted")
    for condition in criteria.get("where", []):
        if condition:
            where_clauses.append(condition)

    meta_params: Dict[str, Any] = {}
    matched_groups = 0
    for word_index, word in enumerate(words):
        # Expand each word into a carefully de-duplicated synonym list.
        seen_variants: set[str] = set()
        variants: List[str] = []
        for candidate in get_word_synonyms(word):
            candidate_text = candidate.strip()
            if not candidate_text:
                continue
            dedup_key = candidate_text.casefold()
            if dedup_key in seen_variants:
                continue
            seen_variants.add(dedup_key)
            variants.append(candidate_text)

        if not variants:
            # If no variants survived the cleanup phase we cannot guarantee a match.
            continue

        clause_parts: List[str] = []
        for variant_index, variant in enumerate(variants):
            param_name = f"meta_word_{word_index}_{variant_index}"
            clause_parts.append(f"{alias}.metatext ILIKE :{param_name}")
            meta_params[param_name] = f"%{variant}%"

        if clause_parts:
            or_clause = " OR ".join(clause_parts)
            where_clauses.append(f"({or_clause})")
            matched_groups += 1

    if matched_groups == 0:
        # No usable clauses were generated, so return early to avoid a cartesian search.
        return []

    order_by_clauses, limit_value, offset_value = get_sql_order_and_limit(
        criteria,
        alias=alias,
        use_textsearch=False,
        rank_expression=None,
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

    base_sql = text("".join(sql_lines))

    sql_params: Dict[str, Any] = dict(criteria.get("params", {}))
    if limit_value is not None:
        sql_params["limit"] = limit_value
    if offset_value:
        sql_params["offset"] = offset_value
    sql_params.update(meta_params)

    # Run the composed SQL and return a mapping-based result set just like other search helpers.
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


def search_items(
    raw_query: str,
    target_uuid: Optional[str] = None,
    context: Any = None,
    primary_key_column: str = "id",
    db_session: Optional[Any] = None,
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
    db_session : Optional[Any]
        Optional SQLAlchemy session to reuse. When omitted, a temporary session
        is created via :func:`session_scope` so connections are reliably
        returned to the pool.

    Returns
    -------
    List[Dict[str, Any]]
        Deduplicated item dictionaries ready for JSON serialization.  Each
        entry includes a ``pk`` (mirroring ``id``) and a ``slug`` computed via
        :func:`backend.app.slugify.slugify`.
    """

    if not (raw_query and raw_query.strip()):
        log.info("search_items: empty query -> returning empty list")
        return _finalize_item_rows([])

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

    def _execute_with_session(session: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        if not target_uuid and len(sq.identifiers) == 1:
            identifier = sq.identifiers[0]

            uuid_candidate: Optional[str]
            try:
                uuid_candidate = normalize_pg_uuid(identifier)
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

        if target_uuid and sq.has_directive("suggest"):
            # Quietly amplify the query text with context from the target item so container suggestions
            # inherit meaningful signals from the item currently being viewed.
            try:
                normalized_target = normalize_pg_uuid(target_uuid)
            except (ValueError, AttributeError, TypeError):
                normalized_target = None

            if normalized_target:
                try:
                    engine = get_engine()
                    target_row = get_db_item_as_dict(engine, "items", normalized_target)
                except (LookupError, ValueError):
                    target_row = None
                except Exception:
                    log.exception("Failed to load target item %s for suggest directive", normalized_target)
                    target_row = None

                if target_row:
                    supplemental_bits: List[str] = []
                    for field_name in ("name", "metatext"):
                        field_value = target_row.get(field_name)
                        if isinstance(field_value, str):
                            trimmed_value = field_value.strip()
                            if trimmed_value:
                                supplemental_bits.append(trimmed_value)

                    if supplemental_bits:
                        invisible_hint = " ".join(supplemental_bits)
                        combined_text = f"{(query_text or '').strip()} {invisible_hint}".strip()
                        query_text = combined_text
                        sq.query_text = combined_text
                        if isinstance(getattr(sq, "query_terms", None), list):
                            sq.query_terms = list(sq.query_terms) + invisible_hint.split()

        normalized_query_text = (query_text or "").strip()
        mergewaiting_directive = sq.has_directive("mergewaiting")
        merge_ready_ids: Optional[set[str]] = None
        if mergewaiting_directive and normalized_query_text and normalized_query_text != "*":
            # Preload the identifiers of merge-ready items so we can filter the
            # standard text-search results without complicating the SQL builder.
            merge_ready_ids = _collect_merge_ready_item_ids(session)

        if sq.has_directive("meta"):
            # Perform a synonym-aware metatext search that insists on matching each word.
            rows = _execute_metatext_search(
                session,
                sq,
                query_text,
                default_table="items",
                default_alias="i",
                default_order_templates=["{alias}.date_last_modified {direction}"],
            )
        else:
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
            if merge_ready_ids is not None:
                identifier = row_dict.get("id") or row_dict.get("pk")
                if identifier is None or str(identifier) not in merge_ready_ids:
                    continue
            if sq.evaluate(row_dict):
                results.append(augment_item_dict(row_dict))

        if target_uuid and sq.has_directive("codematched"):
            matched_ids = find_code_matched_items(target_uuid)
            # Hydrate and prepend code-matched results ahead of standard search items.
            append_code_matched_items(results, matched_ids, augment_row=augment_item_dict)

        if sq.has_directive("pinned"):
            append_pinned_items(
                session,
                "items",
                results,
                augment_row=augment_item_dict,
            )

        if mergewaiting_directive:
            # Fetch the merge-aware identifier set if we skipped it earlier (for
            # example, when the query text was empty and the SQL branch handled the
            # heavy lifting) so pinned items and other additions still honor the
            # directive.
            if merge_ready_ids is None:
                merge_ready_ids = _collect_merge_ready_item_ids(session)

            active_merge_ids = merge_ready_ids if merge_ready_ids else set()
            filtered_results: List[Dict[str, Any]] = []
            for item in results:
                identifier = item.get("pk") or item.get("id")
                if identifier is None:
                    continue
                if str(identifier) in active_merge_ids:
                    filtered_results.append(item)
            results[:] = filtered_results

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

    if db_session is not None:
        return _execute_with_session(db_session)

    with session_scope() as session:
        return _execute_with_session(session)


def search_invoices(
    raw_query: str,
    target_uuid: Optional[str] = None,
    context: Any = None,
    primary_key_column: str = "id",
    db_session: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    if not (raw_query and raw_query.strip()):
        log.info("search_invoices: empty query -> returning empty list")
        return _finalize_invoice_rows([])

    if isinstance(context, dict):
        sq_context = dict(context)
    else:
        sq_context = {}
        if context is not None:
            sq_context["payload"] = context
    sq_context.setdefault("table", "invoices")
    sq_context.setdefault("table_alias", "inv")
    sq = SearchQuery(s=raw_query, context=sq_context)

    def _execute_with_session(session: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        if not target_uuid and len(sq.identifiers) == 1:
            identifier = sq.identifiers[0]

            uuid_candidate: Optional[str]
            try:
                uuid_candidate = normalize_pg_uuid(identifier)
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

        if sq.has_directive("pinned"):
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

    if db_session is not None:
        return _execute_with_session(db_session)

    with session_scope() as session:
        return _execute_with_session(session)



@bp.route("/pinsummary", methods=["GET"])
@login_required
def pin_summary_api():
    """Return how many items and invoices are still considered opened pins."""
    try:
        with session_scope() as session:
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
        include_thumbnails = to_bool(data.get("include_thumbnails"))

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
            with session_scope() as session:
                thumbnail_map = get_item_thumbnails(
                    (item.get("pk") for item in items),
                    db_session=session,
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
