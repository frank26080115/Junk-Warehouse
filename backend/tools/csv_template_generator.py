"""Utility to generate a CSV template for the items table."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import get_engine, get_column_types

TABLE_NAME = "items"
IGNORED_COLUMNS: List[str] = [
    "id",
    "short_id",
    "textsearch",
    "date_creation",
    "date_last_modified",
]


def placeholder_for(column: str, column_type: str) -> str:
    normalized = column_type.lower()
    if column == "is_staging":
        return "true"
    if "boolean" in normalized:
        return "false"
    if any(token in normalized for token in ("timestamp", "date")):
        return "2024-01-01T00:00:00Z"
    if any(token in normalized for token in ("int", "numeric", "decimal", "double", "real")):
        return "0"
    return f"Example {column}"


def generate_template(output_path: Path) -> Path:
    engine = get_engine()
    column_types: Dict[str, str] = get_column_types(engine, TABLE_NAME)
    columns = [
        column
        for column in column_types
        if column not in IGNORED_COLUMNS
    ]

    if "is_staging" not in columns and "is_staging" in column_types:
        columns.append("is_staging")

    placeholders = [placeholder_for(column, column_types[column]) for column in columns]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(columns)
        writer.writerow(placeholders)

    return output_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a CSV template for the items table.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / "items_table_template.csv",
        help="Where to write the generated template (default: ./items_table_template.csv).",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        output_path = generate_template(args.output)
    except Exception as exc:
        print(f"Failed to generate template: {exc}", file=sys.stderr)
        return 1

    print(f"Template written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
