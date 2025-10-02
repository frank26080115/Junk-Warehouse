"""
This file helps with "associations" between items
items can be related to other items, and these relationships can have an association type
the `assoc_type` is an integer represented by bits
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List, Tuple

from sqlalchemy import text

from .db import get_engine, get_db_item_as_dict, update_db_row_by_dict
from .helpers import normalize_pg_uuid
from .history import log_history
# from .embeddings import update_embeddings_for_item # circular import if done here

log = logging.getLogger(__name__)

CONTAINMENT_BIT = 1
RELATED_BIT = 2
SIMILAR_BIT = 4
MERGE_BIT = 8

ALL_ASSOCIATION_BITS = (
    CONTAINMENT_BIT,
    RELATED_BIT,
    SIMILAR_BIT,
    MERGE_BIT,
)
ALL_ASSOCIATION_MASK = 0
for _bit in ALL_ASSOCIATION_BITS:
    ALL_ASSOCIATION_MASK |= _bit

default_words = {
    CONTAINMENT_BIT: "containment",
    RELATED_BIT: "related",
    SIMILAR_BIT: "similar",
    MERGE_BIT: "merge",
}
BIT_TO_WORD = dict(default_words)
WORD_TO_BIT = {value: key for key, value in BIT_TO_WORD.items()}
BIT_TO_EMOJI_HTML_ENTITY = {
    CONTAINMENT_BIT: "&#x1F5C3;",
    RELATED_BIT: "&#x1F517;",
    SIMILAR_BIT: "&#x1F46F;",
    MERGE_BIT: "&#x1F91D;",
}
BIT_TO_EMOJI_CHARACTER = {
    CONTAINMENT_BIT: "🗃️",
    RELATED_BIT: "🔗",
    SIMILAR_BIT: "👯",
    MERGE_BIT: "🤝",
}


def bit_to_word(bit: int) -> str:
    return BIT_TO_WORD.get(bit, "")


def word_to_bit(word: str | None) -> int:
    if word is None:
        return 0
    normalized = word.strip().lower()
    return WORD_TO_BIT.get(normalized, 0)


def bit_to_emoji_html_entity(bit: int) -> str:
    return BIT_TO_EMOJI_HTML_ENTITY.get(bit, "")


def bit_to_emoji_character(bit: int) -> str:
    return BIT_TO_EMOJI_CHARACTER.get(bit, "")


def int_has_containment(value: int) -> bool:
    return bool(value & CONTAINMENT_BIT)


def int_has_related(value: int) -> bool:
    return bool(value & RELATED_BIT)


def int_has_similar(value: int) -> bool:
    return bool(value & SIMILAR_BIT)


def int_has_merge(value: int) -> bool:
    return bool(value & MERGE_BIT)


def collect_words_from_int(value: int) -> list[str]:
    return [BIT_TO_WORD[bit] for bit in ALL_ASSOCIATION_BITS if value & bit]


def collect_emoji_characters_from_int(value: int) -> list[str]:
    return [BIT_TO_EMOJI_CHARACTER[bit] for bit in ALL_ASSOCIATION_BITS if value & bit]


def collect_emoji_entities_from_int(value: int) -> list[str]:
    return [BIT_TO_EMOJI_HTML_ENTITY[bit] for bit in ALL_ASSOCIATION_BITS if value & bit]


def get_item_relationship(first_identifier: Any, second_identifier: Any) -> Optional[Dict[str, Any]]:
    """Retrieve or consolidate a relationship record between two items."""

    try:
        normalized_first = normalize_pg_uuid(str(first_identifier))
        normalized_second = normalize_pg_uuid(str(second_identifier))
    except Exception as exc:
        log.debug(
            "Unable to normalize relationship identifiers %r and %r: %s",
            first_identifier,
            second_identifier,
            exc,
        )
        return None

    engine = get_engine()
    with engine.begin() as conn:
        selection = conn.execute(
            text(
                """
                SELECT id, item_id, assoc_id, assoc_type
                FROM relationships
                WHERE (item_id = :first_id AND assoc_id = :second_id)
                   OR (item_id = :second_id AND assoc_id = :first_id)
                ORDER BY id
                """
            ),
            {"first_id": normalized_first, "second_id": normalized_second},
        ).mappings().all()

        if not selection:
            return None

        if len(selection) == 1:
            return dict(selection[0])

        combined_bits = 0
        for row in selection:
            try:
                combined_bits |= int(row.get("assoc_type") or 0)
            except (TypeError, ValueError):
                combined_bits |= 0

        desired_item_id = normalized_first
        desired_assoc_id = normalized_second
        target_row: Optional[Dict[str, Any]] = None
        for row in selection:
            if str(row.get("item_id")) == desired_item_id and str(row.get("assoc_id")) == desired_assoc_id:
                target_row = dict(row)
                break

        if target_row is None:
            first_row = dict(selection[0])
            conn.execute(
                text(
                    """
                    UPDATE relationships
                    SET item_id = :item_id, assoc_id = :assoc_id
                    WHERE id = :relationship_id
                    """
                ),
                {
                    "item_id": desired_item_id,
                    "assoc_id": desired_assoc_id,
                    "relationship_id": first_row.get("id"),
                },
            )
            target_row = first_row

        conn.execute(
            text(
                """
                UPDATE relationships
                SET assoc_type = :assoc_type
                WHERE id = :relationship_id
                """
            ),
            {
                "assoc_type": combined_bits,
                "relationship_id": target_row.get("id"),
            },
        )

        for row in selection:
            if row.get("id") == target_row.get("id"):
                continue
            conn.execute(
                text(
                    """
                    DELETE FROM relationships
                    WHERE id = :relationship_id
                    """
                ),
                {"relationship_id": row.get("id")},
            )

        final_row = conn.execute(
            text(
                """
                SELECT id, item_id, assoc_id, assoc_type
                FROM relationships
                WHERE id = :relationship_id
                """
            ),
            {"relationship_id": target_row.get("id")},
        ).mappings().first()

        return dict(final_row) if final_row else None


def set_item_relationship(first_identifier: Any, second_identifier: Any, assoc_type: int) -> Optional[Dict[str, Any]]:
    """Create or update a relationship while respecting existing directionality."""

    try:
        normalized_first = normalize_pg_uuid(str(first_identifier))
        normalized_second = normalize_pg_uuid(str(second_identifier))
    except Exception as exc:
        log.debug(
            "Unable to normalize relationship identifiers %r and %r for insert: %s",
            first_identifier,
            second_identifier,
            exc,
        )
        return None

    existing = get_item_relationship(normalized_first, normalized_second)
    
    if existing is None:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO relationships (item_id, assoc_id, assoc_type)
                    VALUES (:item_id, :assoc_id, :assoc_type)
                    """
                ),
                {
                    "item_id": normalized_first,
                    "assoc_id": normalized_second,
                    "assoc_type": int(assoc_type),
                },
            )

        x = get_item_relationship(normalized_first, normalized_second)
        log_history(item_id_1=normalized_first, item_id_2=normalized_second, event="new relationship", meta=x)
        return x

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE relationships
                SET assoc_type = :assoc_type
                WHERE id = :relationship_id
                """
            ),
            {
                "assoc_type": int(assoc_type),
                "relationship_id": existing.get("id"),
            },
        )

    if True: # int_has_containment(int(assoc_type)):
        from .embeddings import update_embeddings_for_item
        try:
            update_embeddings_for_item(normalized_first)
        except Exception:
            log.exception(f"While calling update_embeddings_for_item from set_item_relationship for {str(normalized_first)}")
        try:
            update_embeddings_for_item(normalized_second)
        except:
            log.exception(f"While calling update_embeddings_for_item from set_item_relationship for {str(normalized_second)}")

    x = get_item_relationship(existing.get("item_id"), existing.get("assoc_id"))
    log_history(item_id_1=normalized_first, item_id_2=normalized_second, event="update relationship", meta={
            "before": existing,
            "after": x
        })
    return x


def move_item(item_identifier: Any, target_container_identifier: Any) -> Dict[str, Any]:
    """Move an item to a different container while keeping containment history tidy."""
    # Normalize identifiers so downstream comparisons remain reliable.
    try:
        normalized_item_id = normalize_pg_uuid(str(item_identifier))
        normalized_target_id = normalize_pg_uuid(str(target_container_identifier))
    except Exception as exc:
        log.debug(
            "move_item: unable to normalize identifiers %r and %r: %s",
            item_identifier,
            target_container_identifier,
            exc,
        )
        return {"ok": False, "error": "Invalid item or container identifier."}

    engine = get_engine()

    # Cache lookups so we do not query the same item repeatedly when examining paths.
    item_cache: Dict[str, Optional[Dict[str, Any]]] = {}

    def _load_item_dict(identifier: str) -> Optional[Dict[str, Any]]:
        """Fetch and cache item rows to avoid repeated queries."""
        if not identifier:
            return None
        if identifier in item_cache:
            return item_cache[identifier]
        try:
            row = get_db_item_as_dict(engine, "items", identifier)
        except Exception:
            log.debug("move_item: failed to load item %s while evaluating containment paths", identifier)
            item_cache[identifier] = None
            return None
        item_cache[identifier] = dict(row)
        return item_cache[identifier]

    moving_item = _load_item_dict(normalized_item_id)
    if not moving_item:
        return {"ok": False, "error": "Item to move could not be found."}

    target_container = _load_item_dict(normalized_target_id)
    if not target_container:
        return {"ok": False, "error": "Target container could not be found."}

    # Ensure our cache retains the canonical copies for the items we already resolved.
    item_cache[normalized_item_id] = moving_item
    item_cache[normalized_target_id] = target_container

    try:
        from .containment_path import fetch_containment_paths

        containment_paths = fetch_containment_paths(normalized_item_id)
    except Exception:
        log.exception("move_item: failed to fetch containment paths for %s", normalized_item_id)
        containment_paths = []

    selected_path: Optional[Dict[str, Any]] = None
    existing_container_id: Optional[str] = None
    deletion_reason: Optional[str] = None

    if containment_paths:
        fixed_paths = [path for path in containment_paths if path.get("terminal_is_fixed_location")]
        if len(fixed_paths) == 1:
            candidate = fixed_paths[0]
            sequence = list(candidate.get("path") or [])
            if sequence:
                try:
                    existing_container_id = normalize_pg_uuid(str(sequence[0]))
                    selected_path = candidate
                    deletion_reason = "unique_fixed_location_path"
                except Exception:
                    log.debug("move_item: skipped fixed-location path with invalid identifier")
        elif len(fixed_paths) > 1:
            deletion_reason = "multiple_fixed_location_paths"
        else:
            filtered_candidates: List[Tuple[Dict[str, Any], str, Dict[str, Any]]] = []
            for candidate in containment_paths:
                sequence = list(candidate.get("path") or [])
                if not sequence:
                    continue
                try:
                    first_identifier = normalize_pg_uuid(str(sequence[0]))
                    terminal_identifier = normalize_pg_uuid(str(sequence[-1]))
                except Exception:
                    continue
                first_item = _load_item_dict(first_identifier)
                if not first_item or not (first_item.get("is_container") or first_item.get("is_collection")):
                    continue
                terminal_item = _load_item_dict(terminal_identifier)
                if not terminal_item or not (terminal_item.get("is_container") or terminal_item.get("is_collection")):
                    continue
                filtered_candidates.append((candidate, first_identifier, first_item))
            if len(filtered_candidates) == 1:
                selected_path, existing_container_id, _ = filtered_candidates[0]
                deletion_reason = "unique_container_path"
            elif len(filtered_candidates) > 1:
                large_candidates = [entry for entry in filtered_candidates if entry[2].get("is_large")]
                if len(large_candidates) == 1:
                    selected_path, existing_container_id, _ = large_candidates[0]
                    deletion_reason = "large_container_path"

    removed_relationship: Optional[Dict[str, Any]] = None
    previous_container: Optional[Dict[str, Any]] = None
    if selected_path and existing_container_id:
        previous_container = _load_item_dict(existing_container_id)
        relationship = get_item_relationship(normalized_item_id, existing_container_id)
        if relationship and int(relationship.get("assoc_type") or 0) & CONTAINMENT_BIT:
            try:
                from .items import delete_item_relationship

                removed_relationship = delete_item_relationship(relationship.get("id"))
            except Exception:
                log.exception("move_item: failed to delete containment relationship %s", relationship.get("id"))
        else:
            log.debug(
                "move_item: no containment relationship found between %s and %s",
                normalized_item_id,
                existing_container_id,
            )

    created_relationship = set_item_relationship(normalized_item_id, normalized_target_id, CONTAINMENT_BIT)

    timestamp_text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _format_descriptor(row: Optional[Dict[str, Any]]) -> str:
        """Return a human-friendly descriptor prioritizing the slug."""
        if not row:
            return "unknown"
        slug_value = str(row.get("slug") or "").strip()
        if slug_value:
            return slug_value
        name_value = str(row.get("name") or "").strip()
        if name_value:
            return name_value
        return str(row.get("id") or "unknown")

    def _extract_slug(row: Optional[Dict[str, Any]]) -> Optional[str]:
        """Return the slug or identifier for history logging."""
        if not row:
            return None
        slug_value = str(row.get("slug") or "").strip()
        if slug_value:
            return slug_value
        identifier_value = row.get("id")
        return str(identifier_value) if identifier_value is not None else None

    from_descriptor = _format_descriptor(previous_container)
    to_descriptor = _format_descriptor(target_container)
    item_descriptor = _format_descriptor(moving_item)

    if removed_relationship:
        remarks_line = f"[{timestamp_text}] moved item from {from_descriptor} to {to_descriptor}."
    else:
        remarks_line = f"[{timestamp_text}] moved item to {to_descriptor}."

    existing_remarks = str(moving_item.get("remarks") or "").rstrip()
    if existing_remarks:
        updated_remarks = f"{existing_remarks}\\r\\n{remarks_line}"
    else:
        updated_remarks = remarks_line

    update_payload, _status_code = update_db_row_by_dict(
        engine=engine,
        table="items",
        uuid=normalized_item_id,
        data={"remarks": updated_remarks},
        fuzzy=False,
    )
    if not (isinstance(update_payload, dict) and update_payload.get("ok")):
        log.debug("move_item: remarks update returned non-success payload: %s", update_payload)

    history_meta = {
        "item": _extract_slug(moving_item),
        "from": _extract_slug(previous_container) if removed_relationship else None,
        "to": _extract_slug(target_container),
    }
    log_history(
        item_id_1=normalized_item_id,
        item_id_2=normalized_target_id,
        event="moved item",
        meta=history_meta,
    )

    try:
        from .embeddings import update_embeddings_for_container

        embedding_targets: Dict[str, Dict[str, Any]] = {}
        for candidate in [moving_item, previous_container, target_container]:
            if not candidate:
                continue
            if candidate.get("is_container") or candidate.get("is_collection"):
                candidate_id = str(candidate.get("id") or "")
                if candidate_id:
                    embedding_targets[candidate_id] = candidate
        for candidate in embedding_targets.values():
            try:
                update_embeddings_for_container(candidate)
            except Exception:
                log.exception("move_item: failed to refresh container embeddings for %s", candidate.get("id"))
    except Exception:
        log.exception("move_item: container embedding update failed to initialize")

    return {
        "ok": True,
        "deleted_relationship": removed_relationship,
        "created_relationship": created_relationship,
        "deletion_reason": deletion_reason,
    }
