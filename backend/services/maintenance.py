# use from project root
# python backend/tools/maintenance.py

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Set, Union

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, delete, or_, select, update
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.logging_setup import start_log

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


def prune_images(target_directory: Union[Path, str]) -> Dict[str, int]:
    """Remove deleted or orphaned images from the database and filesystem."""
    engine = get_engine()
    tables = _load_tables(engine, ("images", "item_images"))
    images_table = tables["images"]
    item_images_table = tables["item_images"]
    target_dir_path = Path(target_directory)

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
