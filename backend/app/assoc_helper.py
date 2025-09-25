from __future__ import annotations

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
