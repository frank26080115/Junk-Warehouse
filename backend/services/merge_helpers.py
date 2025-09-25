from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from sqlalchemy import Table, delete, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

Executor = Union[Connection, Session]

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

def _combine_notes(existing: str, addition: str) -> str:
    existing_text = (existing or "").strip()
    addition_text = (addition or "").strip()
    if not addition_text:
        return existing_text
    if not existing_text:
        return addition_text
    existing_parts = {part.strip() for part in existing_text.split(SECTION_BREAK)}
    if addition_text in existing_parts:
        return existing_text
    return f"{existing_text}{SECTION_BREAK}{addition_text}"


def _relationship_sort_key(
    row: Dict[str, Any],
    target_item: uuid.UUID,
    target_assoc: uuid.UUID,
) -> Tuple[int, float, str]:
    matches = row.get("item_id") == target_item and row.get("assoc_id") == target_assoc
    timestamp = _normalize_datetime(row.get("date_updated"))
    ts_value = -(timestamp.timestamp()) if timestamp else float("inf")
    return (0 if matches else 1, ts_value, str(row.get("id")))


def _reassign_relationships(
    executor: Executor,
    relationships_table: Table,
    primary_uuid: uuid.UUID,
    secondary_uuid: uuid.UUID,
) -> Dict[str, int]:
    stmt = (
        select(
            relationships_table.c.id,
            relationships_table.c.item_id,
            relationships_table.c.assoc_id,
            relationships_table.c.assoc_type,
            relationships_table.c.notes,
            relationships_table.c.date_updated,
        )
        .where(
            or_(
                relationships_table.c.item_id.in_([primary_uuid, secondary_uuid]),
                relationships_table.c.assoc_id.in_([primary_uuid, secondary_uuid]),
            )
        )
    )
    rows = [dict(row) for row in executor.execute(stmt).mappings().all()]

    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    deletions: List[uuid.UUID] = []
    updated_rows = 0
    reassigned_rows = 0

    for row in rows:
        current_item = row.get("item_id")
        current_assoc = row.get("assoc_id")
        new_item = primary_uuid if current_item == secondary_uuid else current_item
        new_assoc = primary_uuid if current_assoc == secondary_uuid else current_assoc

        if new_item == new_assoc:
            deletions.append(row["id"])
            continue

        key = (str(new_item), str(new_assoc))
        entry = grouped.setdefault(
            key,
            {
                "target_item": new_item,
                "target_assoc": new_assoc,
                "rows": [],
                "assoc_type": 0,
                "notes": "",
            },
        )
        entry["rows"].append(row)
        entry["assoc_type"] |= int(row.get("assoc_type") or 0)
        entry["notes"] = _combine_notes(entry["notes"], row.get("notes") or "")

    for entry in grouped.values():
        target_item = entry["target_item"]
        target_assoc = entry["target_assoc"]
        rows_for_key = entry["rows"]
        rows_for_key.sort(
            key=lambda row: _relationship_sort_key(row, target_item, target_assoc)
        )
        keeper = rows_for_key[0]
        desired_values: Dict[str, Any] = {}
        if keeper.get("item_id") != target_item or keeper.get("assoc_id") != target_assoc:
            desired_values["item_id"] = target_item
            desired_values["assoc_id"] = target_assoc
            reassigned_rows += 1
        if int(keeper.get("assoc_type") or 0) != entry["assoc_type"]:
            desired_values["assoc_type"] = entry["assoc_type"]
        normalized_notes = entry["notes"].strip()
        if normalized_notes and normalized_notes != (keeper.get("notes") or "").strip():
            desired_values["notes"] = normalized_notes
        if desired_values:
            executor.execute(
                update(relationships_table)
                .where(relationships_table.c.id == keeper["id"])
                .values(**desired_values)
            )
            updated_rows += 1
        for redundant in rows_for_key[1:]:
            deletions.append(redundant["id"])

    deleted_count = 0
    if deletions:
        unique_deletions = tuple(dict.fromkeys(deletions))
        executor.execute(
            delete(relationships_table).where(relationships_table.c.id.in_(unique_deletions))
        )
        deleted_count = len(unique_deletions)

    summary = {
        "scanned": len(rows),
        "updated": updated_rows,
        "reassigned": reassigned_rows,
        "deleted": deleted_count,
    }
    log.debug("Relationship merge summary: %s", summary)
    return summary


def _merge_item_images(
    executor: Executor,
    item_images_table: Table,
    primary_uuid: uuid.UUID,
    secondary_uuid: uuid.UUID,
) -> Dict[str, int]:
    stmt = (
        select(
            item_images_table.c.id,
            item_images_table.c.item_id,
            item_images_table.c.img_id,
            item_images_table.c.rank,
            item_images_table.c.date_updated,
        )
        .where(item_images_table.c.item_id.in_([primary_uuid, secondary_uuid]))
    )
    rows = [dict(row) for row in executor.execute(stmt).mappings().all()]

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("img_id"))
        groups.setdefault(key, []).append(row)

    deletions: List[uuid.UUID] = []
    updates: List[Tuple[uuid.UUID, Dict[str, Any]]] = []
    kept_rows: List[Dict[str, Any]] = []

    for entries in groups.values():
        entries.sort(
            key=lambda row: (
                row.get("item_id") != primary_uuid,
                row.get("rank") is None,
                row.get("rank") if row.get("rank") is not None else 0,
                str(row.get("id")),
            )
        )
        keeper = entries[0]
        kept_rows.append(dict(keeper))
        for redundant in entries[1:]:
            deletions.append(redundant["id"])

    kept_rows.sort(
        key=lambda row: (
            row.get("rank") if row.get("rank") is not None else float("inf"),
            _normalize_datetime(row.get("date_updated")) or datetime.min,
            str(row.get("id")),
        )
    )

    reassigned = 0
    for new_rank, row in enumerate(kept_rows):
        values: Dict[str, Any] = {}
        if row.get("item_id") != primary_uuid:
            values["item_id"] = primary_uuid
            reassigned += 1
        if row.get("rank") != new_rank:
            values["rank"] = new_rank
        if values:
            updates.append((row["id"], values))

    for row_id, values in updates:
        executor.execute(
            update(item_images_table).where(item_images_table.c.id == row_id).values(**values)
        )

    deleted_count = 0
    if deletions:
        unique_deletions = tuple(dict.fromkeys(deletions))
        executor.execute(
            delete(item_images_table).where(item_images_table.c.id.in_(unique_deletions))
        )
        deleted_count = len(unique_deletions)

    summary = {
        "scanned": len(rows),
        "reassigned": reassigned,
        "ranked": len(updates),
        "deleted": deleted_count,
    }
    log.debug("Image merge summary: %s", summary)
    return summary


def _transfer_invoice_links(
    executor: Executor,
    invoice_items_table: Table,
    primary_uuid: uuid.UUID,
    secondary_uuid: uuid.UUID,
) -> Dict[str, int]:
    stmt = (
        select(
            invoice_items_table.c.id,
            invoice_items_table.c.item_id,
            invoice_items_table.c.invoice_id,
        )
        .where(invoice_items_table.c.item_id.in_([primary_uuid, secondary_uuid]))
    )
    rows = [dict(row) for row in executor.execute(stmt).mappings().all()]

    existing_invoices = {row["invoice_id"] for row in rows if row.get("item_id") == primary_uuid}
    updates: List[uuid.UUID] = []
    deletions: List[uuid.UUID] = []

    for row in rows:
        if row.get("item_id") != secondary_uuid:
            continue
        invoice_id = row.get("invoice_id")
        if invoice_id in existing_invoices:
            deletions.append(row["id"])
        else:
            existing_invoices.add(invoice_id)
            updates.append(row["id"])

    for row_id in updates:
        executor.execute(
            update(invoice_items_table)
            .where(invoice_items_table.c.id == row_id)
            .values(item_id=primary_uuid)
        )

    deleted_count = 0
    if deletions:
        unique_deletions = tuple(dict.fromkeys(deletions))
        executor.execute(
            delete(invoice_items_table).where(invoice_items_table.c.id.in_(unique_deletions))
        )
        deleted_count = len(unique_deletions)

    summary = {
        "scanned": len(rows),
        "reassigned": len(updates),
        "deleted": deleted_count,
    }
    log.debug("Invoice link transfer summary: %s", summary)
    return summary


def _transfer_embeddings(
    executor: Executor,
    embeddings_table: Table,
    primary_uuid: uuid.UUID,
    secondary_uuid: uuid.UUID,
) -> Dict[str, int]:
    stmt = (
        select(
            embeddings_table.c.item_id,
            embeddings_table.c.model,
            embeddings_table.c.vec,
            embeddings_table.c.date_updated,
        )
        .where(embeddings_table.c.item_id.in_([primary_uuid, secondary_uuid]))
    )
    rows = [dict(row) for row in executor.execute(stmt).mappings().all()]

    primary_row = next((row for row in rows if row.get("item_id") == primary_uuid), None)
    secondary_row = next((row for row in rows if row.get("item_id") == secondary_uuid), None)

    updated = False
    reassigned = False
    deleted = False

    if secondary_row is None:
        summary = {"scanned": len(rows), "reassigned": 0, "updated": 0, "deleted": 0}
        log.debug("Embedding transfer summary: %s", summary)
        return summary

    if primary_row is None:
        executor.execute(
            update(embeddings_table)
            .where(embeddings_table.c.item_id == secondary_uuid)
            .values(item_id=primary_uuid)
        )
        reassigned = True
    else:
        primary_ts = _normalize_datetime(primary_row.get("date_updated"))
        secondary_ts = _normalize_datetime(secondary_row.get("date_updated"))
        prefer_secondary = False
        if secondary_ts and primary_ts:
            prefer_secondary = secondary_ts > primary_ts
        elif secondary_ts and not primary_ts:
            prefer_secondary = True
        if prefer_secondary:
            executor.execute(
                update(embeddings_table)
                .where(embeddings_table.c.item_id == primary_uuid)
                .values(
                    model=secondary_row.get("model"),
                    vec=secondary_row.get("vec"),
                    date_updated=secondary_row.get("date_updated"),
                )
            )
            updated = True
        executor.execute(
            delete(embeddings_table).where(embeddings_table.c.item_id == secondary_uuid)
        )
        deleted = True

    summary = {
        "scanned": len(rows),
        "reassigned": int(reassigned),
        "updated": int(updated),
        "deleted": int(deleted),
    }
    log.debug("Embedding transfer summary: %s", summary)
    return summary


def _build_merge_audit_note(
    primary_uuid: uuid.UUID,
    secondary_uuid: uuid.UUID,
    primary_item: Dict[str, Any],
    secondary_item: Dict[str, Any],
) -> str:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    iso_time = timestamp.isoformat().replace("+00:00", "Z")
    primary_name = (primary_item.get("name") or "").strip() or str(primary_uuid)
    secondary_name = (secondary_item.get("name") or "").strip() or str(secondary_uuid)
    primary_short = primary_item.get("short_id")
    secondary_short = secondary_item.get("short_id")
    primary_label = f" {primary_short}" if primary_short is not None else ""
    secondary_label = f" {secondary_short}" if secondary_short is not None else ""
    return (
        f"{iso_time} merged {secondary_name}{secondary_label} ({secondary_uuid}) into "
        f"{primary_name}{primary_label} ({primary_uuid})."
    )


def _append_audit_note(existing: str, audit_note: str) -> str:
    existing_text = (existing or "").strip()
    audit_text = (audit_note or "").strip()
    if not audit_text:
        return existing_text
    if not existing_text:
        return audit_text
    existing_segments = {part.strip() for part in existing_text.split(SECTION_BREAK)}
    if audit_text in existing_segments:
        return existing_text
    return f"{existing_text}{SECTION_BREAK}{audit_text}"


def _ensure_index_refresh(
    executor: Executor,
    items_table: Table,
    primary_uuid: uuid.UUID,
) -> datetime:
    timestamp = datetime.utcnow()
    executor.execute(
        update(items_table)
        .where(items_table.c.id == primary_uuid)
        .values(date_last_modified=timestamp)
    )
    return timestamp


__all__ = [
    "_merge_name",
    "_merge_description",
    "_merge_remarks",
    "_merge_product_code",
    "_merge_url",
    "_merge_source",
    "_merge_quantity",
    "_merge_metatext",
    "_reassign_relationships",
    "_merge_item_images",
    "_transfer_invoice_links",
    "_transfer_embeddings",
    "_build_merge_audit_note",
    "_append_audit_note",
    "_ensure_index_refresh",
]
