from __future__ import annotations

import logging
import uuid
from collections import deque
from typing import Any, Deque, Iterable, List, Mapping, Sequence

from sqlalchemy import text

from .assoc_helper import CONTAINMENT_BIT
from .db import get_engine
from .helpers import normalize_pg_uuid, coerce_identifier_to_uuid

log = logging.getLogger(__name__)


def get_all_containments(item_identifier: Any) -> List[str]:
    """Return UUIDs connected to the target item by containment relationships.

    The query inspects both directions of the relationship so the caller does
    not need to know whether the item was stored as ``item_id`` or ``assoc_id``.
    Duplicate entries are filtered out to keep the result clean for consumers.
    """

    normalized = normalize_pg_uuid(item_identifier)

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

    return [normalize_pg_uuid(row) for row in rows]


def fetch_containment_paths(item_identifier: Any) -> List[dict[str, Any]]:
    """Return every containment path reachable from the provided item.

    The implementation relies on a PostgreSQL recursive CTE so the database can
    efficiently explore the containment graph.  Paths terminate when they reach
    either an item marked as ``is_fixed_location`` or when no further
    containment relationships are available (a dead end).  Each entry contains
    both the UUID path and a matching list of item names so the caller can
    present user-friendly breadcrumbs.
    """

    normalized = normalize_pg_uuid(item_identifier)
    # Ensure we have a consistently formatted UUID string for comparison.
    target_uuid_str = normalize_pg_uuid(normalized)

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
              AND (
                  -- Prevent recursion from walking past fixed locations unless we are still evaluating the original target item.
                  NOT containment_tree.is_fixed_location
                  OR containment_tree.current_id = :target
              )
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
            normalized_path = [normalize_pg_uuid(value) for value in raw_path]
            name_list = [str(value) if value is not None else "" for value in raw_names]
            # The recursive query returns the target item at the start of the path.
            # Remove that entry so callers only see the surrounding containment items.
            start_index = 1 if normalized_path and normalized_path[0] == target_uuid_str else 0
            trimmed_path = normalized_path[start_index:]
            trimmed_names = name_list[start_index:]
            if not trimmed_path:
                # A path that only referenced the target itself conveys no useful
                # containment information, so we skip returning it altogether.
                continue
            results.append(
                {
                    "path": trimmed_path,
                    "names": trimmed_names,
                    "terminal_is_fixed_location": bool(row.get("is_fixed_location")),
                    "terminal_is_dead_end": bool(row.get("is_dead_end")),
                }
            )

    return results


def are_items_contaiment_chained(first_item: Any, second_item: Any) -> bool:
    """Return True when ``second_item`` appears in a containment path from ``first_item``."""
    normalized_first = coerce_identifier_to_uuid(first_item)
    if not normalized_first:
        log.debug("Unable to determine a UUID for the first item; containment chain lookup skipped.")
        return False

    normalized_second = coerce_identifier_to_uuid(second_item)
    if not normalized_second:
        log.debug("Unable to determine a UUID for the second item; containment chain lookup skipped.")
        return False

    if normalized_first == normalized_second:
        # A containment path is only meaningful when referencing two distinct items.
        return False

    for path_details in fetch_containment_paths(normalized_first):
        path_candidates = path_details.get("path") or []
        if normalized_second in path_candidates:
            return True

    return False

