from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Tuple, Any

SEPARATOR = ";"

SECTION_BREAK = "\r\n\r\n#####\r\n\r\n"

def _normalize_datetime(candidate: Any) -> Optional[datetime]:
    if isinstance(candidate, datetime):
        return candidate
    if isinstance(candidate, str):
        text = candidate.strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = f"{text[:-1]}+00:00"
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None

def _prioritized_values(
    primary_value: str,
    secondary_value: str,
    primary_created: Optional[Any],
    secondary_created: Optional[Any],
) -> Tuple[str, str]:
    primary_ts = _normalize_datetime(primary_created)
    secondary_ts = _normalize_datetime(secondary_created)
    if primary_ts and secondary_ts:
        if primary_ts <= secondary_ts:
            return primary_value, secondary_value
        return secondary_value, primary_value
    if primary_ts and not secondary_ts:
        return primary_value, secondary_value
    if secondary_ts and not primary_ts:
        return secondary_value, primary_value
    return primary_value, secondary_value

def _merge_name(
    primary: str,
    secondary: str,
    *,
    primary_created: Optional[Any] = None,
    secondary_created: Optional[Any] = None,
) -> str:
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
    prioritized, alternate = _prioritized_values(
        primary_value,
        secondary_value,
        primary_created,
        secondary_created,
    )
    return prioritized or alternate

def _merge_description(
    primary: str,
    secondary: str,
    *,
    primary_created: Optional[Any] = None,
    secondary_created: Optional[Any] = None,
) -> str:
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
    first, second = _prioritized_values(
        primary_value,
        secondary_value,
        primary_created,
        secondary_created,
    )
    ordered = [value for value in (first, second) if value]
    return SECTION_BREAK.join(ordered)

def _merge_remarks(
    primary: str,
    secondary: str,
    *,
    primary_created: Optional[Any] = None,
    secondary_created: Optional[Any] = None,
) -> str:
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
    first, second = _prioritized_values(
        primary_value,
        secondary_value,
        primary_created,
        secondary_created,
    )
    ordered = [value for value in (first, second) if value]
    return SECTION_BREAK.join(ordered)

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
