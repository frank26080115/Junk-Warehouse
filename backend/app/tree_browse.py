"""Helpers for building the initial containment tree for the UI."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Mapping

from sqlalchemy import text

from .assoc_helper import CONTAINMENT_BIT
from .db import get_engine, get_db_item_as_dict

log = logging.getLogger(__name__)

ITEMS_TABLE = "items"


def _normalize_db_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a JSON-serializable copy of a database row."""

    normalized: Dict[str, Any] = {}
    # Copy every value into a new dictionary so the caller never mutates the original row.
    for key, value in dict(row).items():
        if isinstance(value, uuid.UUID):
            normalized[key] = str(value)
        elif isinstance(value, datetime):
            normalized[key] = value.isoformat()
        elif isinstance(value, date):
            normalized[key] = value.isoformat()
        else:
            normalized[key] = value
    if "id" in normalized and normalized["id"] is not None:
        normalized["id"] = str(normalized["id"])
    normalized.setdefault("child_nodes", [])
    return normalized


def _fetch_child_identifiers(connection: Any, parent_id: str) -> List[str]:
    """Return the UUID strings for immediate containment children of ``parent_id``."""

    # This query keeps the selection focused on immediate children so the UI can expand lazily.
    child_query = text(
        """
        SELECT assoc_id
        FROM relationships
        WHERE item_id = :parent_id
          AND (COALESCE(assoc_type, 0) & :containment_bit) <> 0
          AND assoc_id IS NOT NULL
        ORDER BY assoc_id
        """
    )
    rows = connection.execute(
        child_query,
        {"parent_id": parent_id, "containment_bit": CONTAINMENT_BIT},
    ).scalars().all()
    identifiers: List[str] = []
    for raw_identifier in rows:
        if not raw_identifier:
            continue
        identifiers.append(str(raw_identifier))
    return identifiers


def get_root_structure() -> Dict[str, Any]:
    """Return containment roots and their first-level children for the UI tree."""

    engine = get_engine()
    structure: Dict[str, Any] = {"root_nodes": []}

    with engine.begin() as connection:
        # Gather every root-level container to seed the client-side tree.
        root_rows = connection.execute(
            text(
                """
                SELECT id
                FROM items
                WHERE is_tree_root = TRUE
                ORDER BY COALESCE(name, ''), id
                """
            )
        ).scalars().all()

        for raw_root_id in root_rows:
            from .items import augment_item_dict
            if not raw_root_id:
                continue
            root_id = str(raw_root_id)
            # Use the shared helper so the row shape matches other item responses.
            try:
                root_row = get_db_item_as_dict(engine, ITEMS_TABLE, root_id)
                root_row = augment_item_dict(root_row)
            except LookupError:
                log.debug("Skipping missing root item %s", root_id)
                continue
            except ValueError:
                log.debug("Skipping invalid root identifier %r", raw_root_id)
                continue

            child_identifiers = _fetch_child_identifiers(connection, root_id)
            child_nodes: List[Dict[str, Any]] = []
            # Resolve each child lazily so missing or malformed entries do not break the entire payload.
            for child_id in child_identifiers:
                try:
                    child_row = get_db_item_as_dict(engine, ITEMS_TABLE, child_id)
                    child_row = augment_item_dict(child_row)
                except LookupError:
                    log.debug("Skipping missing child item %s", child_id)
                    continue
                except ValueError:
                    log.debug("Skipping invalid child identifier %r", child_id)
                    continue
                normalized_child = _normalize_db_row(child_row)
                normalized_child["child_nodes"] = []
                child_nodes.append(normalized_child)

            normalized_root = _normalize_db_row(root_row)
            normalized_root["child_nodes"] = child_nodes
            structure["root_nodes"].append(normalized_root)

    return structure
