"""Batch importer for the items table."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import MetaData, Table, insert

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import get_engine, get_column_types
from app.items import insert_item

TABLE_NAME = "items"
IGNORED_COLUMNS: Sequence[str] = (
    "id",
    "short_id",
    "textsearch",
    "date_creation",
    "date_last_modified",
)
BOOLEAN_TRUE = {"1", "true", "t", "yes", "y"}
BOOLEAN_FALSE = {"0", "false", "f", "no", "n"}


def coerce_value(raw_value: str, column_type: str, column_name: str) -> Any:
    text_value = raw_value.strip()
    if text_value == "":
        return None

    normalized = column_type.lower()
    if "boolean" in normalized:
        lowered = text_value.lower()
        if lowered in BOOLEAN_TRUE:
            return True
        if lowered in BOOLEAN_FALSE:
            return False
        raise ValueError(
            f"Column '{column_name}': '{raw_value}' is not a valid boolean value",
        )

    if any(token in normalized for token in ("int", "serial", "bigint", "smallint")):
        try:
            return int(text_value)
        except ValueError as exc:
            raise ValueError(
                f"Column '{column_name}': '{raw_value}' is not a valid integer",
            ) from exc

    if any(token in normalized for token in ("numeric", "decimal", "double", "real")):
        try:
            return float(text_value)
        except ValueError as exc:
            raise ValueError(
                f"Column '{column_name}': '{raw_value}' is not a valid number",
            ) from exc

    return raw_value


def parse_header(
    header: Sequence[str],
    column_types: Dict[str, str],
) -> tuple[List[Optional[str]], List[str], List[str]]:
    header_map: List[Optional[str]] = []
    used_columns: List[str] = []
    skipped_columns: List[str] = []
    seen: set[str] = set()

    for entry in header:
        name = entry.strip()
        if not name:
            header_map.append(None)
            continue
        if name in IGNORED_COLUMNS:
            skipped_columns.append(name)
            header_map.append(None)
            continue
        if name not in column_types:
            skipped_columns.append(name)
            header_map.append(None)
            continue
        header_map.append(name)
        if name not in seen:
            used_columns.append(name)
            seen.add(name)

    return header_map, used_columns, skipped_columns


def build_row(
    values: Sequence[str],
    header_map: Sequence[Optional[str]],
    column_types: Dict[str, str],
    row_number: int,
) -> Optional[Dict[str, Any]]:
    row_data: Dict[str, Any] = {}
    non_empty = False

    for index, cell in enumerate(values):
        if index >= len(header_map):
            break
        column_name = header_map[index]
        if column_name is None:
            continue
        text_value = cell.strip()
        if text_value == "":
            continue
        non_empty = True
        column_type = column_types[column_name]
        coerced = coerce_value(text_value, column_type, column_name)
        row_data[column_name] = coerced

    if not non_empty:
        return None

    return row_data


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch import items from a CSV file")
    parser.add_argument("csv_path", type=Path, help="Path to the CSV file to import")
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Encoding to use when reading the CSV (default: utf-8-sig)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the CSV and report actions without inserting records",
    )
    parser.add_argument(
        "--sql-output",
        type=Path,
        help="Write INSERT statements to the given SQL file instead of executing them",
    )
    parser.add_argument(
        "--container-uuid",
        help="Automatically relate each new item to the given container UUID",
    )
    parser.add_argument(
        "--invoice-uuid",
        help="Automatically relate each new item to the given invoice UUID",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    csv_path: Path = args.csv_path
    if not csv_path.exists():
        logging.error("CSV file %s does not exist", csv_path)
        return 1
    if not csv_path.is_file():
        logging.error("%s is not a file", csv_path)
        return 1

    engine = get_engine()
    column_types = get_column_types(engine, TABLE_NAME)

    sql_output_path: Optional[Path] = args.sql_output
    container_uuid: Optional[str] = getattr(args, "container_uuid", None)
    invoice_uuid: Optional[str] = getattr(args, "invoice_uuid", None)

    metadata = MetaData()
    sql_file = None
    items_table: Optional[Table] = None
    relationships_table: Optional[Table] = None
    invoice_items_table: Optional[Table] = None

    if sql_output_path is not None:
        items_table = Table(TABLE_NAME, metadata, autoload_with=engine)
        sql_output_path.parent.mkdir(parents=True, exist_ok=True)
        sql_file = sql_output_path.open("w", encoding="utf-8", newline="")
        sql_file.write("-- SQL insert statements generated by batch_items_importer\r\n")
        sql_file.write("BEGIN;\r\n")

    if container_uuid:
        relationships_table = Table("relationships", metadata, autoload_with=engine)

    if invoice_uuid:
        invoice_items_table = Table("invoice_items", metadata, autoload_with=engine)

    successes = 0
    failures = 0

    try:
        with csv_path.open("r", newline="", encoding=args.encoding) as csv_file:
            reader = csv.reader(csv_file, quoting=csv.QUOTE_MINIMAL)
            try:
                header = next(reader)
            except StopIteration:
                logging.error("CSV file is empty")
                return 1

            header_map, used_columns, skipped = parse_header(header, column_types)
            if not used_columns:
                logging.error("None of the CSV columns match the items table")
                return 1

            logging.info("Columns to import: %s", ", ".join(used_columns))
            if skipped:
                logging.info(
                    "Skipping columns: %s",
                    ", ".join(sorted(set(skipped))),
                )

            for row_number, row in enumerate(reader, start=2):
                try:
                    row_data = build_row(row, header_map, column_types, row_number)
                except ValueError as exc:
                    failures += 1
                    logging.error("%s", exc)
                    continue

                if not row_data:
                    continue

                row_data["is_staging"] = True

                if (container_uuid or invoice_uuid) and "id" not in row_data:
                    # Generate a deterministic identifier so relationship rows can reference the new item.
                    row_data["id"] = str(uuid.uuid4())

                if args.dry_run:
                    logging.info("[dry-run] Row %d would insert %s", row_number, row_data)
                    if container_uuid:
                        logging.info("[dry-run] Row %d would relate item %s to container %s", row_number, row_data.get("id", "<pending>"), container_uuid)
                    if invoice_uuid:
                        logging.info("[dry-run] Row %d would relate item %s to invoice %s", row_number, row_data.get("id", "<pending>"), invoice_uuid)
                    successes += 1
                    continue

                if sql_file is not None and items_table is not None:
                    statement = items_table.insert().values(**row_data)
                    compiled = statement.compile(
                        dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                    )
                    sql_file.write(str(compiled) + ";\r\n")

                    if container_uuid and relationships_table is not None:
                        item_identifier = row_data.get("id")
                        if not item_identifier:
                            raise RuntimeError("Missing item id for container relationship generation")
                        rel_statement = insert(relationships_table).values(
                            item_id=item_identifier,
                            assoc_id=container_uuid,
                            assoc_type=1,  # Bit 0 marks containment relationships
                        )
                        rel_compiled = rel_statement.compile(
                            dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                        )
                        sql_file.write(str(rel_compiled) + ";\r\n")

                    if invoice_uuid and invoice_items_table is not None:
                        item_identifier = row_data.get("id")
                        if not item_identifier:
                            raise RuntimeError("Missing item id for invoice relationship generation")
                        invoice_statement = insert(invoice_items_table).values(
                            item_id=item_identifier,
                            invoice_id=invoice_uuid,
                        )
                        invoice_compiled = invoice_statement.compile(
                            dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                        )
                        sql_file.write(str(invoice_compiled) + ";\r\n")

                    successes += 1
                    logging.info("Row %d queued for SQL output", row_number)
                    continue

                try:
                    inserted_item = insert_item(row_data, engine=engine)
                except Exception as exc:
                    failures += 1
                    logging.error("Row %d failed: %s", row_number, exc)
                    continue

                inserted_id = str(inserted_item.get("id") or row_data.get("id") or "")

                if (container_uuid or invoice_uuid) and not inserted_id:
                    raise RuntimeError("Inserted item identifier missing; cannot create relationships")

                if container_uuid or invoice_uuid:
                    with engine.begin() as connection:
                        if container_uuid and relationships_table is not None:
                            connection.execute(
                                insert(relationships_table).values(
                                    item_id=inserted_id,
                                    assoc_id=container_uuid,
                                    assoc_type=1,  # Bit 0 marks containment relationships
                                )
                            )
                        if invoice_uuid and invoice_items_table is not None:
                            connection.execute(
                                insert(invoice_items_table).values(
                                    item_id=inserted_id,
                                    invoice_id=invoice_uuid,
                                )
                            )

                    if container_uuid:
                        logging.info("Row %d linked item %s to container %s", row_number, inserted_id, container_uuid)
                    if invoice_uuid:
                        logging.info("Row %d linked item %s to invoice %s", row_number, inserted_id, invoice_uuid)

                successes += 1
                logging.info("Row %d inserted", row_number)
    except Exception as exc:
        logging.error("Failed to process CSV: %s", exc)
        return 1

    finally:
        if sql_file is not None:
            sql_file.write("COMMIT;\r\n")
            sql_file.close()

    logging.info("Import finished: %d succeeded, %d failed", successes, failures)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())


