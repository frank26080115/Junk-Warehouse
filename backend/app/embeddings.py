# backend/app/embeddings.py

from __future__ import annotations

import hashlib
import logging
import random
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.orm import Session

from .db import get_engine, get_db_item_as_dict, get_or_create_session
from .assoc_helper import CONTAINMENT_BIT
from .helpers import normalize_pg_uuid
from .search_expression import SearchQuery, get_sql_order_and_limit
from ..automation.ai_helpers import EmbeddingAi
from .containment_path import get_all_containments

log = logging.getLogger(__name__)

DEFAULT_EMBEDDING_LIMIT = 50

def _resolve_item_dict(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> Dict[str, Any]:
    """Return a dictionary representing the requested item with a normalized string id.

    Callers may supply a fully populated mapping, a UUID instance, or any identifier that
    can be coerced into a UUID string. This helper hides the retrieval and normalization
    details so that downstream embedding logic can depend on a predictable structure.
    """
    engine = get_engine()
    if isinstance(item_or_identifier, Mapping):
        # Copy the mapping so we can safely adjust fields without mutating caller data.
        item_dict = dict(item_or_identifier)
        raw_id = item_dict.get("id")
        if raw_id is None:
            raise ValueError("Item dictionary must include an 'id' field")
        if isinstance(raw_id, uuid.UUID):
            item_uuid = raw_id
        else:
            item_uuid = uuid.UUID(normalize_pg_uuid(str(raw_id)))
        # Ensure the identifier uses the canonical string form understood by PostgreSQL.
        item_dict["id"] = str(item_uuid)
        return item_dict

    if isinstance(item_or_identifier, uuid.UUID):
        item_uuid = item_or_identifier
    else:
        item_uuid = uuid.UUID(normalize_pg_uuid(str(item_or_identifier)))
    # Fetch the latest database row for the item so embeddings always reflect persisted data.
    item_dict = get_db_item_as_dict(engine, "items", item_uuid)
    item_dict["id"] = str(item_uuid)
    return item_dict


def _collect_item_text(item_row: Mapping[str, Any]) -> str:
    """Concatenate the key descriptive fields for an item into a single text blob.

    Embeddings work better when multiple descriptive attributes are fed into the
    generator. This helper gathers the name, description, and metatext in a predictable
    order, trimming whitespace and ignoring missing values so the resulting text is
    stable and free from accidental gaps.
    """
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


def _build_embedding_vector(ai: EmbeddingAi, text_input: str, *, dimensions: int = 384) -> List[float]:
    return ai.build_embedding_vector(text_input, dimensions = dimensions)[0]

def get_embeddings_vector_for(item_identifier: Union[str, uuid.UUID]) -> Optional[Dict[str, Any]]:
    """Return the persisted embedding vector for the requested item, if available."""

    item_uuid = uuid.UUID(normalize_pg_uuid(str(item_identifier)))
    engine = get_engine()
    metadata = MetaData()
    embeddings_table = Table("item_embeddings", metadata, autoload_with=engine)
    with engine.begin() as conn:
        result = conn.execute(
            select(embeddings_table.c.model, embeddings_table.c.vec).where(embeddings_table.c.item_id == item_uuid).limit(1)
        ).first()
        if not result:
            result = update_embeddings_for_item(item_uuid)
        if result:
            # Normalize to a dictionary so callers have a predictable structure.
            record = dict(result)
            return {"model": record["model"], "vector": record["vec"]}
    return None

def update_embeddings_for_item(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> dict:
    try:
        item_dict = _resolve_item_dict(item_or_identifier)
    except Exception:
        log.exception("Failed to resolve item for embedding update")
        raise

    text_input = _collect_item_text(item_dict)
    ai = EmbeddingAi()
    vector = _build_embedding_vector(ai, text_input)

    item_uuid = uuid.UUID(normalize_pg_uuid(item_dict.get("id")))

    engine = get_engine()
    metadata = MetaData()
    embeddings_table = Table("item_embeddings", metadata, autoload_with=engine)

    values = {"model": ai.model, "vec": vector}

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

        # Re-select the row so we can return the fresh model and vector payload.
        existing = conn.execute(
            select(embeddings_table.c.model, embeddings_table.c.vec, embeddings_table.c.item_id)
            .where(embeddings_table.c.item_id == item_uuid)
            .limit(1)
        ).first()

    # Containers and collections have an additional embedding that summarizes their contents.
    if item_dict.get("is_container") or item_dict.get("is_collection"):
        try:
            update_embeddings_for_container(item_dict)
        except Exception:
            log.exception("Failed to update container embeddings for item %s", item_uuid)

    return dict(existing) if existing else None


def update_embeddings_for_container(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> None:
    """Rebuild the container_embeddings entry for the requested container item."""

    try:
        item_dict = _resolve_item_dict(item_or_identifier)
    except Exception:
        log.exception("Failed to resolve container for embedding update")
        raise

    container_uuid = uuid.UUID(normalize_pg_uuid(item_dict.get("id")))
    engine = get_engine()

    # Collect the container's own embedding and all descendant embeddings so we can
    # compute a representative summary vector. We skip any missing entries because an
    # embedding may not exist yet for every item.
    collected_vectors: List[Tuple[str, Dict[str, Any]]] = []

    container_embedding = get_embeddings_vector_for(container_uuid)
    if container_embedding:
        collected_vectors.append((str(container_uuid), container_embedding))

    for child_uuid in get_all_containments(container_uuid):
        child_embedding = get_embeddings_vector_for(child_uuid)
        if child_embedding:
            collected_vectors.append((str(child_uuid), child_embedding))

    if not collected_vectors:
        log.warning("No embeddings available to summarize container %s", container_uuid)
        return

    model_name = collected_vectors[0][1]["model"]

    # Prepare an accumulator for the arithmetic mean. The dimensions are determined by
    # the first vector because all embeddings generated by the same model share a fixed
    # length.
    vector_length = len(collected_vectors[0][1]["vector"])
    totals: List[float] = [0.0] * vector_length
    contributing_vectors = 0

    for source_id, entry in collected_vectors:
        vector = entry["vector"]
        if entry["model"] != model_name:
            log.warning(
                "Skipping embedding for %s because model %s does not match expected %s",
                source_id,
                entry["model"],
                model_name,
            )
            continue
        if len(vector) != vector_length:
            log.warning(
                "Skipping embedding for %s due to mismatched vector length %s (expected %s)",
                source_id,
                len(vector),
                vector_length,
            )
            continue
        for index, value in enumerate(vector):
            totals[index] += float(value)
        contributing_vectors += 1

    if not contributing_vectors:
        log.warning("No compatible embeddings found while summarizing container %s", container_uuid)
        return

    reciprocal = 1.0 / float(contributing_vectors)
    mean_vector = [component * reciprocal for component in totals]

    metadata = MetaData()
    embeddings_table = Table("container_embeddings", metadata, autoload_with=engine)
    values = {"model": model_name, "vec": mean_vector}

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

    ai = EmbeddingAi()
    vector = _build_embedding_vector(ai, query_text)

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
