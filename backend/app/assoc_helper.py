from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import text

from .db import get_engine
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
