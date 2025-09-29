# backend/app/embeddings.py

from __future__ import annotations

import hashlib
import logging
import random
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.orm import Session

from .db import get_engine, get_db_item_as_dict, get_or_create_session
from .assoc_helper import CONTAINMENT_BIT
from .helpers import normalize_pg_uuid
from .search_expression import SearchQuery, get_sql_order_and_limit

log = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 384
EMBEDDING_MODEL_NAME = "hash-embed-v1"
DEFAULT_EMBEDDING_LIMIT = 50

def _resolve_item_dict(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> Dict[str, Any]:
    engine = get_engine()
    if isinstance(item_or_identifier, Mapping):
        item_dict = dict(item_or_identifier)
        raw_id = item_dict.get("id")
        if raw_id is None:
            raise ValueError("Item dictionary must include an 'id' field")
        if isinstance(raw_id, uuid.UUID):
            item_uuid = raw_id
        else:
            item_uuid = uuid.UUID(normalize_pg_uuid(str(raw_id)))
        item_dict["id"] = str(item_uuid)
        return item_dict

    if isinstance(item_or_identifier, uuid.UUID):
        item_uuid = item_or_identifier
    else:
        item_uuid = uuid.UUID(normalize_pg_uuid(str(item_or_identifier)))
    item_dict = get_db_item_as_dict(engine, "items", item_uuid)
    item_dict["id"] = str(item_uuid)
    return item_dict


def _collect_item_text(item_row: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for column in ("name", "description", "metatext"):
        value = item_row.get(column)
        if not value:
            continue
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                parts.append(trimmed)
        else:
            parts.append(str(value))
    return "".join(parts)


def _build_embedding_vector(text_input: str, *, dimensions: int = EMBEDDING_DIMENSIONS) -> List[float]:
    if not text_input:
        return [0.0] * dimensions
    digest = hashlib.sha512(text_input.encode("utf-8")).digest()
    seed = int.from_bytes(digest, "big")
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dimensions)]


def update_embeddings_for_item(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> None:
    try:
        item_dict = _resolve_item_dict(item_or_identifier)
    except Exception:
        log.exception("Failed to resolve item for embedding update")
        raise

    text_input = _collect_item_text(item_dict)
    vector = _build_embedding_vector(text_input)

    item_uuid = uuid.UUID(str(item_dict.get("id")))

    engine = get_engine()
    metadata = MetaData()
    embeddings_table = Table("item_embeddings", metadata, autoload_with=engine)

    values = {"model": EMBEDDING_MODEL_NAME, "vec": vector}

    with engine.begin() as conn:
        existing = conn.execute(
            select(embeddings_table.c.item_id).where(embeddings_table.c.item_id == item_uuid).limit(1)
        ).first()
        if existing:
            conn.execute(
                embeddings_table.update().where(embeddings_table.c.item_id == item_uuid).values(**values)
            )
        else:
            values_with_id = dict(values)
            values_with_id["item_id"] = item_uuid
            conn.execute(embeddings_table.insert().values(**values_with_id))

    # Containers and collections have an additional embedding that summarizes their contents.
    if item_dict.get("is_container") or item_dict.get("is_collection"):
        try:
            update_embeddings_for_container(item_dict)
        except Exception:
            log.exception("Failed to update container embeddings for item %s", item_uuid)


def update_embeddings_for_container(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> None:
    """Rebuild the container_embeddings entry for the requested container item."""

    try:
        item_dict = _resolve_item_dict(item_or_identifier)
    except Exception:
        log.exception("Failed to resolve container for embedding update")
        raise

    container_uuid = uuid.UUID(str(item_dict.get("id")))
    engine = get_engine()

    # Start with the container's own descriptive text so the embedding reflects the container itself.
    text_fragments: List[str] = []
    base_text = _collect_item_text(item_dict)
    if base_text:
        text_fragments.append(base_text)

    containment_sql = text(
        """
        SELECT assoc_id
        FROM relationships
        WHERE item_id = :container_id
          AND (COALESCE(assoc_type, 0) & :containment_bit) <> 0
        """
    )

    related_ids: List[str] = []
    seen_related: set[str] = set()
    with engine.begin() as conn:
        rows = conn.execute(
            containment_sql,
            {"container_id": container_uuid, "containment_bit": CONTAINMENT_BIT},
        ).scalars().all()
        for identifier in rows:
            if not identifier:
                continue
            normalized = str(identifier)
            if normalized == str(container_uuid):
                continue
            if normalized in seen_related:
                continue
            related_ids.append(normalized)
            seen_related.add(normalized)

    # Gather names and metatext for each contained item. Descriptions are intentionally ignored.
    for related_id in related_ids:
        try:
            related_item = get_db_item_as_dict(engine, "items", related_id)
        except (LookupError, ValueError):
            continue
        if related_item.get("is_deleted"):
            continue
        trimmed_item = dict(related_item)
        trimmed_item["description"] = ""
        fragment = _collect_item_text(trimmed_item)
        if fragment:
            text_fragments.append(fragment)

    text_input = " ".join(fragment for fragment in text_fragments if fragment)
    vector = _build_embedding_vector(text_input)

    metadata = MetaData()
    embeddings_table = Table("container_embeddings", metadata, autoload_with=engine)
    values = {"model": EMBEDDING_MODEL_NAME, "vec": vector}

    with engine.begin() as conn:
        existing = conn.execute(
            select(embeddings_table.c.item_id).where(embeddings_table.c.item_id == container_uuid).limit(1)
        ).first()
        if existing:
            conn.execute(
                embeddings_table.update().where(embeddings_table.c.item_id == container_uuid).values(**values)
            )
        else:
            values_with_id = dict(values)
            values_with_id["item_id"] = container_uuid
            conn.execute(embeddings_table.insert().values(**values_with_id))


def _extract_query_text(query: Union[SearchQuery, str]) -> str:
    if isinstance(query, SearchQuery):
        candidate = (query.query_text or "").strip()
        if not candidate and getattr(query, "raw", None):
            candidate = str(query.raw).strip()
        return candidate
    return str(query or "").strip()


def search_items_by_embeddings(
    search_query_or_text: Union[SearchQuery, str],
    *,
    session: Optional[Session] = None,
    limit: Optional[int] = None,
    embedding_table: str = "item_embeddings",
) -> List[Mapping[str, Any]]:
    query_text = _extract_query_text(search_query_or_text)
    if not query_text:
        return []

    vector = _build_embedding_vector(query_text)

    if session is None:
        session = get_or_create_session()

    default_limit = DEFAULT_EMBEDDING_LIMIT if limit is None else max(int(limit), 1)

    # Validate the caller-supplied table so the dynamically constructed SQL remains safe.
    if embedding_table not in {"item_embeddings", "container_embeddings"}:
        raise ValueError("Unsupported embedding table requested")

    embedding_alias = "ie" if embedding_table == "item_embeddings" else "ce"

    table_name = "items"
    alias = "i"
    where_clauses: List[str] = [f"NOT {alias}.is_deleted"]
    order_by_clauses: List[str] = ["embedding_distance ASC", f"{alias}.date_last_modified DESC"]
    limit_value: Optional[int] = default_limit
    offset_value: Optional[int] = None
    params: Dict[str, Any] = {"query_vec": vector}

    if isinstance(search_query_or_text, SearchQuery):
        criteria = search_query_or_text.get_sql_conditionals()

        table_name = criteria.get("table", table_name)
        alias = criteria.get("table_alias") or alias

        touched_columns = criteria.get("touched_columns") or set()
        where_clauses = []
        if "is_deleted" not in touched_columns:
            where_clauses.append(f"NOT {alias}.is_deleted")
        for condition in criteria.get("where", []):
            if condition:
                where_clauses.append(condition)

        order_by_extra, limit_value, offset_value = get_sql_order_and_limit(
            criteria,
            alias=alias,
            use_textsearch=False,
            rank_expression=None,
            default_order_templates=["{alias}.date_last_modified {direction}"],
            default_limit=default_limit,
        )
        order_by_clauses = ["embedding_distance ASC"] + list(order_by_extra or [])
        if len(order_by_clauses) == 1:
            order_by_clauses.append(f"{alias}.date_last_modified DESC")

        params.update(criteria.get("params", {}))
    else:
        params["limit"] = limit_value

    sql_lines = [
        "SELECT",
        f"    {alias}.*,",
        f"    ({embedding_alias}.vec <=> :query_vec) AS embedding_distance",
        f"FROM {embedding_table} AS {embedding_alias}",
        f"JOIN {table_name} AS {alias} ON {alias}.id = {embedding_alias}.item_id",
    ]

    if where_clauses:
        sql_lines.append("WHERE")
        sql_lines.append(f"    {where_clauses[0]}")
        for clause in where_clauses[1:]:
            sql_lines.append(f"    AND {clause}")

    if order_by_clauses:
        sql_lines.append("ORDER BY")
        for index, clause in enumerate(order_by_clauses):
            prefix = "    " if index == 0 else "    , "
            sql_lines.append(f"{prefix}{clause}")

    if limit_value is not None:
        sql_lines.append("LIMIT :limit")
        params.setdefault("limit", limit_value)

    if offset_value:
        sql_lines.append("OFFSET :offset")
        params["offset"] = offset_value

    sql = text("\n".join(sql_lines))

    rows = session.execute(sql, params).mappings().all()
    return rows
