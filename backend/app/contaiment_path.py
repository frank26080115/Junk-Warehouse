from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable, List, Mapping, Sequence

from sqlalchemy import text

from .assoc_helper import CONTAINMENT_BIT
from .db import get_engine
from .helpers import normalize_pg_uuid

log = logging.getLogger(__name__)


def _normalize_identifier(value: Any) -> str:
    """Normalize arbitrary identifier inputs into a canonical UUID string.

    This helper accepts UUID instances, dictionaries containing an ``id`` key,
    and any other object that can be coerced into text for normalization.  A
    ``ValueError`` is raised when the value cannot be interpreted as a UUID so
    callers can provide a helpful error message upstream.
    """

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, Mapping):
        if "id" in value:
            return _normalize_identifier(value["id"])
        if "uuid" in value:
            return _normalize_identifier(value["uuid"])

    if value is None:
        raise ValueError("Identifier is required to resolve containment data.")

    return normalize_pg_uuid(str(value))


def get_all_containments(item_identifier: Any) -> List[str]:
    """Return UUIDs connected to the target item by containment relationships.

    The query inspects both directions of the relationship so the caller does
    not need to know whether the item was stored as ``item_id`` or ``assoc_id``.
    Duplicate entries are filtered out to keep the result clean for consumers.
    """

    normalized = _normalize_identifier(item_identifier)

    engine = get_engine()
    with engine.begin() as conn:
        rows: Sequence[str] = conn.execute(
            text(
                """
                SELECT DISTINCT
                    CASE
                        WHEN item_id = :target THEN assoc_id
                        ELSE item_id
                    END AS related_id
                FROM relationships
                WHERE (item_id = :target OR assoc_id = :target)
                  AND (COALESCE(assoc_type, 0) & :containment_bit) <> 0
                  AND CASE
                        WHEN item_id = :target THEN assoc_id
                        ELSE item_id
                      END IS NOT NULL
                ORDER BY related_id
                """
            ),
            {"target": normalized, "containment_bit": CONTAINMENT_BIT},
        ).scalars().all()

    return [str(uuid.UUID(str(row))) for row in rows]


def fetch_containment_paths(item_identifier: Any) -> List[dict[str, Any]]:
    """Return every containment path reachable from the provided item.

    The implementation relies on a PostgreSQL recursive CTE so the database can
    efficiently explore the containment graph.  Paths terminate when they reach
    either an item marked as ``is_fixed_location`` or when no further
    containment relationships are available (a dead end).  Each entry contains
    both the UUID path and a matching list of item names so the caller can
    present user-friendly breadcrumbs.
    """

    normalized = _normalize_identifier(item_identifier)

    engine = get_engine()
    sql = text(
        """
        WITH RECURSIVE containment_tree AS (
            SELECT
                i.id AS current_id,
                ARRAY[i.id] AS path,
                ARRAY[i.name] AS name_path,
                i.is_fixed_location
            FROM items AS i
            WHERE i.id = :target
        UNION ALL
            SELECT
                neighbor.id AS current_id,
                containment_tree.path || neighbor.id AS path,
                containment_tree.name_path || neighbor.name AS name_path,
                neighbor.is_fixed_location
            FROM containment_tree
            JOIN relationships AS r
              ON (r.item_id = containment_tree.current_id OR r.assoc_id = containment_tree.current_id)
             AND (COALESCE(r.assoc_type, 0) & :containment_bit) <> 0
            JOIN items AS neighbor
              ON neighbor.id = CASE
                    WHEN r.item_id = containment_tree.current_id THEN r.assoc_id
                    ELSE r.item_id
                END
            WHERE NOT neighbor.id = ANY(containment_tree.path)
        ),
        terminal_paths AS (
            SELECT
                containment_tree.path,
                containment_tree.name_path,
                containment_tree.current_id,
                containment_tree.is_fixed_location,
                NOT EXISTS (
                    SELECT 1
                    FROM relationships AS r2
                    JOIN items AS neighbor2
                      ON neighbor2.id = CASE
                            WHEN r2.item_id = containment_tree.current_id THEN r2.assoc_id
                            ELSE r2.item_id
                        END
                    WHERE (COALESCE(r2.assoc_type, 0) & :containment_bit) <> 0
                      AND (r2.item_id = containment_tree.current_id OR r2.assoc_id = containment_tree.current_id)
                      AND NOT neighbor2.id = ANY(containment_tree.path)
                ) AS is_dead_end
            FROM containment_tree
        )
        SELECT
            path,
            name_path,
            is_fixed_location,
            is_dead_end
        FROM terminal_paths
        WHERE is_fixed_location OR is_dead_end
        ORDER BY cardinality(path) DESC, path
        """
    )

    results: List[dict[str, Any]] = []
    with engine.begin() as conn:
        for row in conn.execute(sql, {"target": normalized, "containment_bit": CONTAINMENT_BIT}).mappings():
            raw_path: Iterable[Any] = row.get("path") or []
            raw_names: Iterable[Any] = row.get("name_path") or []
            normalized_path = [str(uuid.UUID(str(value))) for value in raw_path]
            name_list = [str(value) if value is not None else "" for value in raw_names]
            results.append(
                {
                    "path": normalized_path,
                    "names": name_list,
                    "terminal_is_fixed_location": bool(row.get("is_fixed_location")),
                    "terminal_is_dead_end": bool(row.get("is_dead_end")),
                }
            )

    return results
