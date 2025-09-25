# use from project root
# python backend/tools/maintenance.py

import logging
import time
import uuid
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Union

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, delete, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.assoc_helper import MERGE_BIT
from app.helpers import normalize_pg_uuid
from app.db import get_engine
from app.logging_setup import start_log
from app.static_server import get_public_html_path

log = logging.getLogger(__name__)

# Load backend/.env explicitly (does nothing if file doesn't exist)
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(DOTENV_PATH, override=False)

MAJOR_TABLES_WITH_SOFT_DELETE: Sequence[str] = ("items", "invoices")


def _load_tables(engine: Engine, table_names: Sequence[str]) -> Dict[str, Table]:
    """Reflect and return SQLAlchemy Table objects for the requested names."""
    metadata = MetaData()
    tables: Dict[str, Table] = {}
    for name in table_names:
        tables[name] = Table(name, metadata, autoload_with=engine)
        log.debug("Reflected table %s", name)
    return tables


def _prepare_relationship_context(
    db: Optional[Union[Engine, Connection, Session]] = None,
) -> tuple[
    Union[Connection, Session],
    Table,
    Callable[[], None],
    AbstractContextManager[object],
]:
    """Resolve a database executor and reflected relationships table.

    Returns a tuple of ``(executor, relationships_table, cleanup, transaction_ctx)``
    where ``executor`` is either a :class:`~sqlalchemy.engine.Connection` or
    :class:`~sqlalchemy.orm.Session`, ``relationships_table`` is the reflected
    SQLAlchemy table, ``cleanup`` is a callable that will close any temporary
    connection created by this helper, and ``transaction_ctx`` is a context
    manager suitable for wrapping write operations.
    """

    session: Optional[Session] = None
    connection: Optional[Connection] = None
    should_close_connection = False

    if isinstance(db, Session):
        session = db
    elif isinstance(db, Connection):
        connection = db
    elif isinstance(db, Engine) or db is None:
        engine = db if isinstance(db, Engine) else get_engine()
        connection = engine.connect()
        should_close_connection = True
    else:
        raise TypeError("db must be an Engine, Connection, Session, or None")

    executor: Optional[Union[Connection, Session]] = session if session is not None else connection
    if executor is None:
        raise RuntimeError("Unable to determine database execution context")

    if session is not None:
        transaction_ctx: AbstractContextManager[object] = (
            session.begin() if not session.in_transaction() else nullcontext()
        )
        bind = session.get_bind()
    else:
        assert connection is not None  # nosec - validated above
        transaction_ctx = (
            connection.begin() if not connection.in_transaction() else nullcontext()
        )
        bind = connection.engine

    metadata = MetaData()
    relationships_table = Table("relationships", metadata, autoload_with=bind)

    def cleanup() -> None:
        if should_close_connection and connection is not None:
            connection.close()

    return executor, relationships_table, cleanup, transaction_ctx


def prune_deleted() -> Dict[str, int]:
    """Permanently delete rows that have already been soft-deleted."""
    engine = get_engine()
    tables = _load_tables(engine, MAJOR_TABLES_WITH_SOFT_DELETE)
    deleted_counts: Dict[str, int] = {}

    with engine.begin() as conn:
        for table_name, table in tables.items():
            if "is_deleted" not in table.c:
                log.warning(
                    "Skipping table %s because it does not have an is_deleted column",
                    table_name,
                )
                continue
            result = conn.execute(delete(table).where(table.c.is_deleted.is_(True)))
            count = result.rowcount or 0
            deleted_counts[table_name] = count
            log.info("Removed %s soft-deleted rows from %s", count, table_name)

    log.info("Soft-delete pruning summary: %s", deleted_counts)
    return deleted_counts


def prune_stale_staging_items(cutoff_date: datetime) -> int:
    """Mark stale staging items as deleted based on a cutoff timestamp."""
    engine = get_engine()
    items_table = _load_tables(engine, ("items",))["items"]

    with engine.begin() as conn:
        result = conn.execute(
            update(items_table)
            .where(items_table.c.is_staging.is_(True))
            .where(items_table.c.is_deleted.is_(False))
            .where(items_table.c.date_last_modified < cutoff_date)
            .values(is_deleted=True)
        )
        count = result.rowcount or 0
        log.info(
            "Marked %s staging items as deleted using cutoff %s",
            count,
            cutoff_date.isoformat(),
        )

    return count


def prune_stale_staging_invoices(cutoff_date: datetime) -> int:
    """Mark unprocessed invoices that have snoozed past the cutoff as deleted."""
    engine = get_engine()
    invoices_table = _load_tables(engine, ("invoices",))["invoices"]

    with engine.begin() as conn:
        result = conn.execute(
            update(invoices_table)
            .where(invoices_table.c.has_been_processed.is_(False))
            .where(invoices_table.c.is_deleted.is_(False))
            .where(invoices_table.c.snooze < cutoff_date)
            .values(is_deleted=True)
        )
        count = result.rowcount or 0
        log.info(
            "Marked %s unprocessed invoices as deleted using cutoff %s",
            count,
            cutoff_date.isoformat(),
        )

    return count


def prune_images(
    target_directory: Union[Path, str, None] = None,
) -> Dict[str, int]:
    """Remove deleted or orphaned images from the database and filesystem.

    If ``target_directory`` is not provided or is blank, the value returned by
    :func:`app.static_server.get_public_html_path` is used.
    """
    engine = get_engine()
    tables = _load_tables(engine, ("images", "item_images"))
    images_table = tables["images"]
    item_images_table = tables["item_images"]
    if target_directory is None:
        base_directory = get_public_html_path() / "imgs"
    elif isinstance(target_directory, Path):
        base_directory = target_directory
    else:
        directory_text = str(target_directory).strip()
        base_directory = Path(directory_text) if directory_text else get_public_html_path()

    target_dir_path = base_directory.resolve()

    if not target_dir_path.exists():
        log.warning(
            "Target directory %s does not exist; continuing with database cleanup.",
            target_dir_path,
        )

    rows: List[dict] = []
    deleted_images = 0
    deleted_item_links = 0

    with engine.begin() as conn:
        join_clause = images_table.outerjoin(
            item_images_table, images_table.c.id == item_images_table.c.img_id
        )
        rows = (
            conn.execute(
                select(
                    images_table.c.id,
                    images_table.c.dir,
                    images_table.c.file_name,
                )
                .select_from(join_clause)
                .where(
                    or_(
                        images_table.c.is_deleted.is_(True),
                        item_images_table.c.id.is_(None),
                    )
                )
                .distinct()
            )
            .mappings()
            .all()
        )
        log.info("Found %s images matching prune criteria", len(rows))

        image_ids = [row["id"] for row in rows]
        if image_ids:
            deleted_item_links = (
                conn.execute(
                    delete(item_images_table).where(
                        item_images_table.c.img_id.in_(image_ids)
                    )
                ).rowcount
                or 0
            )
            deleted_images = (
                conn.execute(
                    delete(images_table).where(images_table.c.id.in_(image_ids))
                ).rowcount
                or 0
            )
            log.info(
                "Deleted %s image rows and %s item-image links from the database",
                deleted_images,
                deleted_item_links,
            )
        else:
            log.info("No images met the criteria for pruning from the database.")

    removed_files = 0
    missing_files = 0
    failed_file_removals = 0
    seen_paths: Set[Path] = set()

    for row in rows:
        relative_dir = Path(row.get("dir") or "")
        file_name = row.get("file_name") or ""
        file_path = (target_dir_path / relative_dir / file_name).resolve()
        try:
            file_path.relative_to(target_dir_path)
        except ValueError:
            log.warning(
                "Skipping image file %s because it is outside of %s",
                file_path,
                target_dir_path,
            )
            continue
        if file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        if file_path.exists():
            try:
                file_path.unlink()
                removed_files += 1
                log.info("Deleted image file %s", file_path)
            except OSError:
                failed_file_removals += 1
                log.exception("Failed to delete image file %s", file_path)
        else:
            missing_files += 1
            log.debug("Image file %s does not exist", file_path)

    summary = {
        "images_removed": deleted_images,
        "item_image_links_removed": deleted_item_links,
        "files_deleted": removed_files,
        "files_missing": missing_files,
        "file_delete_errors": failed_file_removals,
    }
    log.info("Completed image pruning with summary %s", summary)
    return summary


def neaten_relationship(
    db: Optional[Union[Engine, Connection, Session]] = None,
    item_uuid: Optional[Union[str, uuid.UUID]] = None,
) -> Dict[str, int]:
    """Merge reciprocal relationships by combining their bit flags.

    This task looks for rows in the ``relationships`` table where two rows
    represent the same association in opposite directions. When found, a single
    row is kept with an ``assoc_type`` value equal to the bitwise OR of all
    matching rows and the remaining duplicates are removed.
    """

    if item_uuid is None:
        target_uuid: Optional[uuid.UUID] = None
    else:
        try:
            target_uuid = item_uuid if isinstance(item_uuid, uuid.UUID) else uuid.UUID(normalize_pg_uuid(item_uuid))
        except (TypeError, ValueError) as exc:
            raise ValueError("item_uuid must be a valid UUID value") from exc

    executor, _, cleanup, transaction_ctx = _prepare_relationship_context(db)

    rows: List[dict] = []
    updates: List[tuple[uuid.UUID, int]] = []
    deletions: List[uuid.UUID] = []
    merged_pairs = 0

    try:
        stmt = select(
            relationships_table.c.id,
            relationships_table.c.item_id,
            relationships_table.c.assoc_id,
            relationships_table.c.assoc_type,
        )
        if target_uuid is not None:
            stmt = stmt.where(
                or_(
                    relationships_table.c.item_id == target_uuid,
                    relationships_table.c.assoc_id == target_uuid,
                )
            )

        rows = executor.execute(stmt).mappings().all()

        groups: Dict[tuple[str, str], List[dict]] = {}
        for row in rows:
            left = str(row["item_id"])
            right = str(row["assoc_id"])
            if left == right:
                continue
            key = (left, right) if left <= right else (right, left)
            groups.setdefault(key, []).append(dict(row))

        for entries in groups.values():
            if len(entries) <= 1:
                continue
            merged_pairs += 1
            entries.sort(key=lambda data: str(data["id"]))
            primary = entries[0]
            combined_type = 0
            for entry in entries:
                combined_type |= int(entry["assoc_type"] or 0)
            if combined_type != int(primary["assoc_type"] or 0):
                updates.append((primary["id"], combined_type))
            for entry in entries[1:]:
                deletions.append(entry["id"])

        with transaction_ctx:
            for row_id, combined in updates:
                executor.execute(
                    update(relationships_table)
                    .where(relationships_table.c.id == row_id)
                    .values(assoc_type=combined)
                )
            if deletions:
                executor.execute(
                    delete(relationships_table).where(
                        relationships_table.c.id.in_(tuple(deletions))
                    )
                )
    finally:
        cleanup()

    return {
        "rows_examined": len(rows),
        "pairs_merged": merged_pairs,
        "rows_updated": len(updates),
        "rows_deleted": len(deletions),
    }


def merge_two_items(
    db: Optional[Union[Engine, Connection, Session]] = None,
    first_item_uuid: Optional[Union[str, uuid.UUID]] = None,
    second_item_uuid: Optional[Union[str, uuid.UUID]] = None,
) -> None:
    """Merge two items that have been flagged with the merge association."""

    if first_item_uuid is None or second_item_uuid is None:
        raise ValueError("Both item UUIDs must be provided for a merge operation")

    try:
        primary_uuid = (
            first_item_uuid
            if isinstance(first_item_uuid, uuid.UUID)
            else uuid.UUID(normalize_pg_uuid(first_item_uuid))
        )
        secondary_uuid = (
            second_item_uuid
            if isinstance(second_item_uuid, uuid.UUID)
            else uuid.UUID(normalize_pg_uuid(second_item_uuid))
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Item UUIDs must be valid UUID values") from exc

    if primary_uuid == secondary_uuid:
        raise ValueError("Cannot merge an item with itself")

    executor, _relationships_table, cleanup, transaction_ctx = _prepare_relationship_context(db)

    try:
        log.info("Preparing to merge items %s and %s", primary_uuid, secondary_uuid)

        with transaction_ctx:
            # TODO: Confirm that both item rows exist and select which one should remain.
            # TODO: Consolidate descriptive fields, notes, and metadata into the surviving item.
            # TODO: Reassign tags, categories, and other relationships from the secondary item.
            # TODO: Merge associated images while avoiding duplicate entries in the item_images table.
            # TODO: Transfer inventory, invoice links, or other domain-specific associations.
            # TODO: Remove or update the initiating merge row from the relationships table.
            # TODO: Soft-delete or archive the secondary item once all data has been transferred.
            # TODO: Trigger any indexing or cache refresh required after the merge completes.
            # TODO: Record an audit trail summarizing the merge for future reference.
    finally:
        cleanup()


def process_pending_merges(
    db: Optional[Union[Engine, Connection, Session]] = None,
) -> int:
    """Scan for merge associations and invoke :func:`merge_two_items` for each."""

    executor, relationships_table, cleanup, _transaction_ctx = _prepare_relationship_context(db)

    processed_pairs = 0
    seen_pairs: Set[frozenset[uuid.UUID]] = set()

    try:
        merge_clause = relationships_table.c.assoc_type.op("&")(MERGE_BIT) != 0
        stmt = select(
            relationships_table.c.item_id,
            relationships_table.c.assoc_id,
            relationships_table.c.assoc_type,
        ).where(merge_clause)

        rows = executor.execute(stmt).mappings().all()
        log.info("Found %s merge relationship candidates", len(rows))

        for row in rows:
            left_raw = row.get("item_id")
            right_raw = row.get("assoc_id")
            if left_raw is None or right_raw is None:
                log.warning("Skipping merge candidate with missing item identifiers: %s", row)
                continue

            try:
                left_uuid = (
                    left_raw
                    if isinstance(left_raw, uuid.UUID)
                    else uuid.UUID(normalize_pg_uuid(str(left_raw)))
                )
                right_uuid = (
                    right_raw
                    if isinstance(right_raw, uuid.UUID)
                    else uuid.UUID(normalize_pg_uuid(str(right_raw)))
                )
            except (TypeError, ValueError):
                log.exception("Encountered merge row with invalid UUIDs: %s", row)
                continue

            if left_uuid == right_uuid:
                log.warning("Skipping self-referential merge candidate for %s", left_uuid)
                continue

            pair_key = frozenset({left_uuid, right_uuid})
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            try:
                merge_two_items(
                    db=executor,
                    first_item_uuid=left_uuid,
                    second_item_uuid=right_uuid,
                )
                processed_pairs += 1
            except Exception:
                log.exception(
                    "Failed to merge items %s and %s flagged for merge",
                    left_uuid,
                    right_uuid,
                )

        return processed_pairs
    finally:
        cleanup()

def main():
    # Initialize logger using your existing setup
    logger = start_log(app_name="maintenance")

    while True:
        try:
            logger.info("Running maintenance task...")

            # TODO: Add your housekeeping logic here
            # e.g., cleaning old sessions, rotating temp files, etc.

        except Exception as e:
            logger.exception("Maintenance loop error")

        time.sleep(60)  # wait 1 minute


if __name__ == "__main__":
    main()