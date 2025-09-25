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
from .helpers import normalize_pg_uuid
from .search_expression import SearchQuery

log = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 384
EMBEDDING_MODEL_NAME = "hash-embed-v1"
DEFAULT_EMBEDDING_LIMIT = 50


def _normalize_uuid(value: Union[str, uuid.UUID]) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    normalized = normalize_pg_uuid(value)
    return uuid.UUID(normalized)


def _resolve_item_dict(item_or_identifier: Union[Mapping[str, Any], str, uuid.UUID]) -> Dict[str, Any]:
    engine = get_engine()
    if isinstance(item_or_identifier, Mapping):
        item_dict = dict(item_or_identifier)
        raw_id = item_dict.get("id")
        if raw_id is None:
            raise ValueError("Item dictionary must include an 'id' field")
        item_uuid = _normalize_uuid(raw_id)
        item_dict["id"] = str(item_uuid)
        return item_dict

    item_uuid = _normalize_uuid(item_or_identifier)
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
) -> List[Mapping[str, Any]]:
    query_text = _extract_query_text(search_query_or_text)
    if not query_text:
        return []

    vector = _build_embedding_vector(query_text)

    if session is None:
        session = get_or_create_session()

    limit_value = DEFAULT_EMBEDDING_LIMIT if limit is None else max(int(limit), 1)

    sql = text(
        """
        SELECT
            i.*,
            (ie.vec <=> :query_vec) AS embedding_distance
        FROM item_embeddings AS ie
        JOIN items AS i ON i.id = ie.item_id
        WHERE NOT i.is_deleted
        ORDER BY embedding_distance ASC, i.date_last_modified DESC
        LIMIT :limit
        """
    )

    params = {"query_vec": vector, "limit": limit_value}
    rows = session.execute(sql, params).mappings().all()
    return rows
