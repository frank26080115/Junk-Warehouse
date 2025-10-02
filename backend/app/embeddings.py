# backend/app/embeddings.py

from __future__ import annotations

import hashlib
import logging
import random
import uuid
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from sqlalchemy import MetaData, Table, select, text, inspect
from sqlalchemy.orm import Session

from .db import get_engine, get_db_item_as_dict, get_or_create_session
from .assoc_helper import CONTAINMENT_BIT
from .helpers import normalize_pg_uuid
from .search_expression import SearchQuery, get_sql_order_and_limit
from ..automation.ai_helpers import EmbeddingAi
from .containment_path import get_all_containments

log = logging.getLogger(__name__)

EMB_TBL_NAME_PREFIX_ITEMS = "items_embeddings"
EMB_TBL_NAME_PREFIX_CONTAINER = "container_embeddings"

DEFAULT_EMBEDDING_LIMIT = 50 # this is the number of results returned per search

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


def _build_embedding_vector(ai: EmbeddingAi, text_input: str) -> List[float]:
    return unit_vect(ai.build_embedding_vector(text_input)[0])

def unit_vect(vec: List[float]) -> List[float]:
    try:
        import numpy as np
        n = np.linalg.norm(vec)
        return vec / n if n > 0 else vec
    except ImportError:
        """Normalize a vector to unit length (L2 norm = 1)."""
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm > 0 else vec

def ensure_embeddings_table_exists(tbl_prefix: str = EMB_TBL_NAME_PREFIX_ITEMS, ai: EmbeddingAi = None) -> str:
    """Create the embedding table for the requested model if it does not already exist."""

    if not ai:
        ai = EmbeddingAi()
    if tbl_prefix not in {EMB_TBL_NAME_PREFIX_ITEMS, EMB_TBL_NAME_PREFIX_CONTAINER}:
        raise ValueError("Unsupported embedding table requested")

    engine = get_engine()
    inspector = inspect(engine)
    suffix = ai.get_as_suffix()
    table_name = f"{tbl_prefix}_{suffix}"

    if inspector.has_table(table_name, schema="public"):
        return table_name

    dimensions = ai.get_dimensions()
    if not isinstance(dimensions, int) or dimensions <= 0:
        raise RuntimeError("Embedding dimensions must be a positive integer before creating tables")

    statements = [
        f"""CREATE TABLE public.{table_name} (
    item_id uuid NOT NULL,
    vec public.vector({dimensions}),
    date_updated timestamp with time zone DEFAULT now() NOT NULL
);""",
        f"CREATE INDEX idx_{table_name}_vec ON public.{table_name} USING hnsw (vec public.vector_cosine_ops) WITH (lists='100');",
        f"""ALTER TABLE ONLY public.{table_name}
    ADD CONSTRAINT {table_name}_pkey PRIMARY KEY (item_id);""",
        f"""ALTER TABLE ONLY public.{table_name}
    ADD CONSTRAINT {table_name}_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.items(id) ON DELETE CASCADE;""",
        f"""CREATE FUNCTION public.touch_{table_name}_updated() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.date_updated := now();
    RETURN NEW;
END;
$$;""",
        f"CREATE TRIGGER trg_touch_{table_name}_updated BEFORE UPDATE ON public.{table_name} FOR EACH ROW EXECUTE FUNCTION public.touch_{table_name}_updated();",
    ]

    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)

    return table_name


def get_embeddings_vector_for(item_identifier: Union[str, uuid.UUID], ai = None) -> Optional[Dict[str, Any]]:
    """Return the persisted embedding vector for the requested item, if available."""

    if not ai:
        ai = EmbeddingAi()

    table_name = ensure_embeddings_table_exists(tbl_prefix=EMB_TBL_NAME_PREFIX_ITEMS, ai=ai)

    item_uuid = uuid.UUID(normalize_pg_uuid(str(item_identifier)))
    engine = get_engine()
    metadata = MetaData()
    embeddings_table = Table(table_name, metadata, autoload_with=engine)
    with engine.begin() as conn:
        result = conn.execute(
            select(embeddings_table.c.vec).where(embeddings_table.c.item_id == item_uuid).limit(1)
        ).first()
        if not result:
            result = update_embeddings_for_item(item_uuid)
        if result:
            # Normalize to a dictionary so callers have a predictable structure.
            record = dict(result)
            return {"vec": record.get("vec")}
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

    table_name = ensure_embeddings_table_exists(tbl_prefix=EMB_TBL_NAME_PREFIX_ITEMS, ai=ai)

    item_uuid = uuid.UUID(normalize_pg_uuid(item_dict.get("id")))

    engine = get_engine()
    metadata = MetaData()
    embeddings_table = Table(table_name, metadata, autoload_with=engine)

    values = {"vec": unit_vect(vector)}

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
            select(embeddings_table.c.vec, embeddings_table.c.item_id)
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


def summarize_container_embeddings(container_uuid,
                                   k_prior: int = 1) -> List[float]:
    """
    Build a container summary embedding using a 'k prior':
    the container's own vector counts as k virtual children.
    k_prior = 0 -> ignore label (contents only)
    k_prior = 1 -> self counts once
    k_prior >=3 -> label remains influential as bin grows
    """
    # 1) collect vectors
    e_self = None
    child_vecs: List[List[float]] = []

    container_embedding = get_embeddings_vector_for(container_uuid, ai=True)
    if not container_embedding:
        container_embedding = update_embeddings_for_item(container_uuid)
    if container_embedding and container_embedding.get("vec"):
        e_self = unit_vect([float(x) for x in container_embedding["vec"]])  # normalize

    for child_uuid in get_all_containments(container_uuid):
        child_embedding = get_embeddings_vector_for(child_uuid, ai=True)
        if child_embedding and child_embedding.get("vec"):
            child_vecs.append(unit([float(x) for x in child_embedding["vec"]]))  # normalize

    if not e_self and not child_vecs:
        log.info("No embeddings available to summarize for container %s", container_uuid)
        return []

    # 2) compute weighted mean
    # sum_children
    if child_vecs:
        vec_len = len(child_vecs[0])
        sum_children = [0.0] * vec_len
        for v in child_vecs:
            for i, val in enumerate(v):
                sum_children[i] += val
        n = len(child_vecs)
    else:
        sum_children, n = None, 0

    if e_self and n > 0:
        # combined = (k * e_self + sum_children) / (k + n)
        combined = [(k_prior * e_self[i] + sum_children[i]) / (k_prior + n) for i in range(len(e_self))]
    elif e_self:
        # only self
        combined = e_self[:]  # already normalized
    else:
        # only children
        combined = [sum_children[i] / n for i in range(len(sum_children))]

    return unit_vect(combined)  # normalize final vector


def update_embeddings_for_container(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> None:
    """Rebuild the container_embeddings entry for the requested container item."""

    try:
        item_dict = _resolve_item_dict(item_or_identifier)
    except Exception:
        log.exception("Failed to resolve container for embedding update")
        raise

    container_uuid = uuid.UUID(normalize_pg_uuid(item_dict.get("id")))
    engine = get_engine()
    ai = EmbeddingAi()
    ensure_embeddings_table_exists(tbl_prefix=EMB_TBL_NAME_PREFIX_ITEMS, ai=ai)
    container_table_name = ensure_embeddings_table_exists(tbl_prefix=EMB_TBL_NAME_PREFIX_CONTAINER, ai=ai)

    metadata = MetaData()
    embeddings_table = Table(container_table_name, metadata, autoload_with=engine)
    values = {"vec": summarize_container_embeddings(container_uuid, ai=ai)}

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
    embedding_table: str = EMB_TBL_NAME_PREFIX_ITEMS,
) -> List[Mapping[str, Any]]:
    query_text = _extract_query_text(search_query_or_text)
    if not query_text:
        return []

    ai = EmbeddingAi()
    ensure_embeddings_table_exists(tbl_prefix=EMB_TBL_NAME_PREFIX_ITEMS, ai=ai)
    ensure_embeddings_table_exists(tbl_prefix=embedding_table, ai=ai)
    vector = _build_embedding_vector(ai, query_text)

    if session is None:
        session = get_or_create_session()

    default_limit = DEFAULT_EMBEDDING_LIMIT if limit is None else max(int(limit), 1)

    # Validate the caller-supplied table so the dynamically constructed SQL remains safe.
    if embedding_table not in {EMB_TBL_NAME_PREFIX_ITEMS, EMB_TBL_NAME_PREFIX_CONTAINER}:
        raise ValueError("Unsupported embedding table requested")

    embedding_alias = "ie" if embedding_table == EMB_TBL_NAME_PREFIX_ITEMS else "ce"

    ai = EmbeddingAi()
    embedding_table = embedding_table + f"_{ai.get_as_suffix()}"

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
