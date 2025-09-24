"""Batch importer for the items table."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import MetaData, Table

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
    sql_file = None
    items_table: Optional[Table] = None
    if sql_output_path is not None:
        items_table = Table(TABLE_NAME, MetaData(), autoload_with=engine)
        sql_output_path.parent.mkdir(parents=True, exist_ok=True)
        sql_file = sql_output_path.open("w", encoding="utf-8", newline="")
        sql_file.write("-- SQL insert statements generated by batch_items_importer\r\n")
        sql_file.write("BEGIN;\r\n")

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

                if args.dry_run:
                    logging.info("[dry-run] Row %d would insert %s", row_number, row_data)
                    successes += 1
                    continue

                if sql_file is not None and items_table is not None:
                    statement = items_table.insert().values(**row_data)
                    compiled = statement.compile(
                        dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                    )
                    sql_file.write(str(compiled) + ";\r\n")
                    successes += 1
                    logging.info("Row %d queued for SQL output", row_number)
                    continue

                try:
                    insert_item(row_data, engine=engine)
                except Exception as exc:
                    failures += 1
                    logging.error("Row %d failed: %s", row_number, exc)
                    continue

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
