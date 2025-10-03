from __future__ import annotations

import logging
import uuid
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Mapping, Sequence

from sqlalchemy import bindparam, text

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
    """Return shortest containment paths from the provided item to fixed locations.

    The previous implementation delegated the full search to PostgreSQL.  The new
    approach gathers the relevant containment relationships, constructs an
    in-memory graph, and performs a breadth-first search so we can enumerate
    *every* shortest path that reaches furniture-like items (``is_fixed_location``).
    When multiple fixed locations share the same minimal distance, each path is
    surfaced.  If no fixed locations are reachable, the function falls back to
    listing immediate neighboring containers to preserve the expectations of
    callers such as :func:`move_items`.
    """

    normalized = normalize_pg_uuid(item_identifier)
    target_uuid_str = normalize_pg_uuid(normalized)

    engine = get_engine()
    with engine.begin() as conn:
        fixed_rows = conn.execute(
            text(
                """
                SELECT id, name
                FROM items
                WHERE is_fixed_location = TRUE
                """
            ),
        ).mappings().all()

        fixed_identifiers = {normalize_pg_uuid(row["id"]) for row in fixed_rows if row.get("id")}

        relationship_rows = conn.execute(
            text(
                """
                SELECT item_id, assoc_id
                FROM relationships
                WHERE (COALESCE(assoc_type, 0) & :containment_bit) <> 0
                  AND item_id IS NOT NULL
                  AND assoc_id IS NOT NULL
                """
            ),
            {"containment_bit": CONTAINMENT_BIT},
        ).mappings().all()

        adjacency: Dict[str, set[str]] = {}
        all_identifiers = {target_uuid_str} | set(fixed_identifiers)
        for row in relationship_rows:
            left_raw = row.get("item_id")
            right_raw = row.get("assoc_id")
            if not left_raw or not right_raw:
                continue
            left_id = normalize_pg_uuid(left_raw)
            right_id = normalize_pg_uuid(right_raw)
            all_identifiers.add(left_id)
            all_identifiers.add(right_id)
            adjacency.setdefault(left_id, set()).add(right_id)
            adjacency.setdefault(right_id, set()).add(left_id)

        metadata: Dict[str, Mapping[str, Any]] = {}
        if all_identifiers:
            meta_sql = text(
                """
                SELECT id, name, is_fixed_location, is_container, is_collection
                FROM items
                WHERE id IN :identifiers
                """
            ).bindparams(bindparam("identifiers", expanding=True))
            metadata_rows = conn.execute(meta_sql, {"identifiers": list(all_identifiers)}).mappings().all()
            for row in metadata_rows:
                normalized_id = normalize_pg_uuid(row.get("id"))
                metadata[normalized_id] = row

    def get_name(item_id: str) -> str:
        details = metadata.get(item_id)
        raw_name = details.get("name") if details else None
        return str(raw_name) if raw_name is not None else ""

    def is_fixed(item_id: str) -> bool:
        details = metadata.get(item_id)
        return bool(details.get("is_fixed_location")) if details else False

    # The breadth-first search computes the shortest distance from the target to
    # every reachable node and remembers the parents that achieve that distance.
    distances: Dict[str, int] = {target_uuid_str: 0}
    parents: Dict[str, List[str]] = {}
    queue: Deque[str] = deque([target_uuid_str])

    while queue:
        current = queue.popleft()
        for neighbor in sorted(adjacency.get(current, set())):
            next_distance = distances[current] + 1
            existing_distance = distances.get(neighbor)
            if existing_distance is None:
                distances[neighbor] = next_distance
                parents[neighbor] = [current]
                queue.append(neighbor)
            elif next_distance == existing_distance:
                parent_list = parents.setdefault(neighbor, [])
                if current not in parent_list:
                    parent_list.append(current)

    def expand_shortest_paths(destination: str, partial: List[str], paths: List[List[str]]) -> None:
        """Recursively expand ``partial`` paths until we reach the target item."""

        if destination == target_uuid_str:
            completed = list(reversed(partial + [destination]))
            paths.append(completed)
            return

        for parent in parents.get(destination, []):
            expand_shortest_paths(parent, partial + [destination], paths)

    all_paths: List[dict[str, Any]] = []
    for fixed_id in sorted(fixed_identifiers):
        if fixed_id == target_uuid_str:
            # The caller only needs neighboring context, so a self-referential
            # path would be redundant noise.
            continue
        if fixed_id not in distances:
            continue

        raw_paths: List[List[str]] = []
        expand_shortest_paths(fixed_id, [], raw_paths)
        for path_with_target in raw_paths:
            trimmed_path = path_with_target[1:]
            if not trimmed_path:
                continue
            name_list = [get_name(item_id) for item_id in trimmed_path]
            all_paths.append(
                {
                    "path": trimmed_path,
                    "names": name_list,
                    "terminal_is_fixed_location": True,
                    "terminal_is_dead_end": False,
                    "distance": len(trimmed_path),
                    "terminal_item_id": fixed_id,
                    "terminal_item_name": get_name(fixed_id),
                }
            )

    all_paths.sort(key=lambda entry: (entry.get("distance", 0), entry.get("path", [])))

    if all_paths:
        return all_paths

    # No fixed locations could be reached, so offer immediate containers to give
    # the caller a sensible fallback.
    neighboring_containers: List[dict[str, Any]] = []
    for neighbor in sorted(adjacency.get(target_uuid_str, set())):
        details = metadata.get(neighbor, {})
        is_container_flag = bool(details.get("is_container") or details.get("is_collection"))
        if not is_container_flag:
            continue
        neighboring_containers.append(
            {
                "path": [neighbor],
                "names": [get_name(neighbor)],
                "terminal_is_fixed_location": is_fixed(neighbor),
                "terminal_is_dead_end": False,
                "distance": 1,
                "terminal_item_id": neighbor,
                "terminal_item_name": get_name(neighbor),
                "fallback_reason": "no_fixed_location_paths",
            }
        )

    return neighboring_containers

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

