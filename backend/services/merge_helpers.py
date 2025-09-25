from __future__ import annotations

from typing import Iterable, List

SEPARATOR = ";"

def _merge_text_with_todo(field_label: str, primary: str, secondary: str) -> str:
    primary_value = (primary or "").strip()
    secondary_value = (secondary or "").strip()
    if not primary_value and secondary_value:
        return secondary_value
    if primary_value and not secondary_value:
        return primary_value
    if not primary_value and not secondary_value:
        return ""
    if primary_value == secondary_value:
        return primary_value
    return f"{primary_value}\nTODO: Resolve merge conflict for {field_label}: {secondary_value}"

def _merge_name(primary: str, secondary: str) -> str:
    return _merge_text_with_todo("name", primary, secondary)

def _merge_description(primary: str, secondary: str) -> str:
    return _merge_text_with_todo("description", primary, secondary)

def _merge_remarks(primary: str, secondary: str) -> str:
    return _merge_text_with_todo("remarks", primary, secondary)

def _deduplicate(values: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        if not value:
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered

def _merge_with_separator(primary: str, secondary: str) -> str:
    deduped = _deduplicate([primary, secondary])
    return SEPARATOR.join(deduped)

def _merge_product_code(primary: str, secondary: str) -> str:
    return _merge_with_separator(primary, secondary)

def _merge_url(primary: str, secondary: str) -> str:
    return _merge_with_separator(primary, secondary)

def _merge_source(primary: str, secondary: str) -> str:
    return _merge_with_separator(primary, secondary)

def _merge_quantity(primary: str, secondary: str) -> str:
    return _merge_with_separator(primary, secondary)

def _merge_metatext(primary: str, secondary: str) -> str:
    words = []
    words.extend((primary or "").split())
    words.extend((secondary or "").split())
    unique_words = _deduplicate(words)
    return " ".join(unique_words)

__all__ = [
    "_merge_name",
    "_merge_description",
    "_merge_remarks",
    "_merge_product_code",
    "_merge_url",
    "_merge_source",
    "_merge_quantity",
    "_merge_metatext",
]
