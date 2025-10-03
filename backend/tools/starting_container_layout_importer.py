"""Import an indented container layout into the items table."""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import pprint
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Ensure the backend package is available whether the script is executed from
# the project root or the backend/tools directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.assoc_helper import CONTAINMENT_BIT
from app.db import get_engine
from app.helpers import clean_item_name
from app.items import insert_item
from app.metatext import update_metatext

LOGGER = logging.getLogger("starting_container_layout_importer")


class LayoutParseError(Exception):
    """Raised when the layout file contains invalid indentation or data."""


@dataclass
class ItemDefinition:
    """Describe an item extracted from the layout file."""

    line_number: int
    depth: int
    raw_name: str
    cleaned_name: str
    description: str
    uuid: uuid.UUID


@dataclass
class ProcessedItem:
    """Track processed items so pins can be released in the right order."""

    definition: ItemDefinition
    payload: dict[str, Any]
    path: str
    parent_id: Optional[str]


def _sql_literal(value: str) -> str:
    """Return a safely quoted SQL literal."""

    return "'" + value.replace("'", "''") + "'"


def _compute_short_id(identifier: uuid.UUID) -> int:
    """Replicate the short id calculation used by ``insert_item``."""

    unsigned_value = int(identifier.hex[-8:], 16) & 0xFFFFFFFF
    if unsigned_value >= 0x80000000:
        return unsigned_value - 0x100000000
    return unsigned_value


def parse_layout_file(layout_path: Path) -> List[ItemDefinition]:
    """Read and validate the indented layout file."""

    if not layout_path.is_file():
        raise LayoutParseError(f"Layout file {layout_path} does not exist or is not a file.")

    raw_text = layout_path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()
    definitions: List[ItemDefinition] = []

    for line_number, raw_line in enumerate(lines, 1):
        stripped_line = raw_line.rstrip()
        if not stripped_line:
            continue

        depth = 0
        while depth < len(stripped_line) and stripped_line[depth] == "	":
            depth += 1
        content = stripped_line[depth:]

        if depth and not definitions:
            raise LayoutParseError(
                f"Line {line_number} is indented but no parent item has been defined yet."
            )

        if content.startswith("	"):
            raise LayoutParseError(
                f"Line {line_number} contains additional tab characters after indentation; please use spaces within the name."
            )

        name_part, _, description_part = content.partition("#")
        name = name_part.strip()
        if not name:
            raise LayoutParseError(f"Line {line_number} does not contain an item name.")

        description = description_part.strip()
        cleaned_name = clean_item_name(name)
        definitions.append(
            ItemDefinition(
                line_number=line_number,
                depth=depth,
                raw_name=name,
                cleaned_name=cleaned_name,
                description=description,
                uuid=uuid.uuid4(),
            )
        )

    if not definitions:
        raise LayoutParseError("The layout file did not contain any items to import.")

    return definitions


class LayoutImporter:
    """Handle the three execution modes requested by the operator."""

    def __init__(self, mode: str) -> None:
        valid_modes = {"execute", "dry-run", "sql"}
        if mode not in valid_modes:
            raise ValueError(f"Mode must be one of {sorted(valid_modes)}; received {mode!r}.")
        self.mode = mode
        self.engine: Optional[Engine] = get_engine() if mode == "execute" else None
        self._pinned_ids: set[str] = set()
        self._sql_lines: List[str] = []
        self._relationship_pairs: List[tuple[str, str]] = []
        self._metatext_placeholder = update_metatext("")

    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC timestamp with second precision."""

        return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()

    def _log(self, message: str) -> None:
        """Log to stdout so the operator sees progress immediately."""

        LOGGER.info(message)

    def ensure_pinned(self, item_id: str, context: str) -> None:
        """Mark a container as open so subsequent inserts inherit it as a parent."""

        if item_id in self._pinned_ids:
            return

        timestamp = self._utc_now_iso()
        if self.mode == "execute":
            assert self.engine is not None
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE items SET pin_as_opened = :stamp WHERE id = :item_id"),
                    {"stamp": timestamp, "item_id": item_id},
                )
            self._log(f"Pinned {context} (item_id={item_id}) at {timestamp}.")
        elif self.mode == "dry-run":
            payload = {"item_id": item_id, "pin_as_opened": timestamp}
            self._log(f"[DRY-RUN] update_pin({pprint.pformat(payload)})")
        else:
            self._sql_lines.append(f"-- Pin {context}")
            self._sql_lines.append(
                f"UPDATE items SET pin_as_opened = '{timestamp}' WHERE id = '{item_id}';"
            )

        self._pinned_ids.add(item_id)

    def release_pin(self, item_id: str, context: str) -> None:
        """Clear the pinned flag once all nested children have been created."""

        if item_id not in self._pinned_ids:
            return

        if self.mode == "execute":
            assert self.engine is not None
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE items SET pin_as_opened = NULL WHERE id = :item_id"),
                    {"item_id": item_id},
                )
            self._log(f"Cleared pin for {context} (item_id={item_id}).")
        elif self.mode == "dry-run":
            self._log(f"[DRY-RUN] clear_pin(item_id='{item_id}')")
        else:
            self._sql_lines.append(f"-- Clear pin for {context}")
            self._sql_lines.append(
                f"UPDATE items SET pin_as_opened = NULL WHERE id = '{item_id}';"
            )

        self._pinned_ids.remove(item_id)

    def _build_payload(self, definition: ItemDefinition) -> dict[str, Any]:
        """Assemble the payload passed to ``insert_item``."""

        return {
            "id": str(definition.uuid),
            "name": definition.cleaned_name,
            "description": definition.description,
            "metatext": "",
            "is_container": True,
            "is_fixed_location": definition.depth == 0,
            "is_staging": False,
        }

    def _record_insert_sql(self, definition: ItemDefinition, path: str) -> None:
        """Generate INSERT statements mirroring ``insert_item``."""

        short_id = _compute_short_id(definition.uuid)
        name_literal = _sql_literal(definition.cleaned_name)
        description_literal = _sql_literal(definition.description)
        metatext_literal = _sql_literal(self._metatext_placeholder)
        fixed_location_literal = "TRUE" if definition.depth == 0 else "FALSE"
        insert_stmt_lines = [
            f"-- {path}",
            "INSERT INTO items (id, short_id, name, description, metatext, is_container, is_fixed_location, is_staging)",
            f"VALUES ('{definition.uuid}', {short_id}, {name_literal}, {description_literal}, {metatext_literal}, TRUE, {fixed_location_literal}, FALSE);",
        ]
        insert_stmt = "\n".join(insert_stmt_lines)
        self._sql_lines.append(insert_stmt)

    def create_item(self, definition: ItemDefinition, path: str) -> dict[str, Any]:
        """Insert the item according to the active mode."""

        payload = self._build_payload(definition)

        if self.mode == "execute":
            assert self.engine is not None
            inserted = insert_item(payload, engine=self.engine, include_pinned_invoices=False)
            self._log(
                f"Inserted {path} (item_id={inserted.get('id')}) with description length {len(definition.description)}."
            )
            return inserted

        if self.mode == "dry-run":
            self._log(
                "[DRY-RUN] insert_item(payload="
                f"{pprint.pformat(payload)}, include_pinned_invoices=False)"
            )
            return {**payload}

        self._record_insert_sql(definition, path)
        return {**payload}

    def process(self, definitions: Sequence[ItemDefinition]) -> List[ProcessedItem]:
        """Create items and build the containment relationships."""

        processed: List[ProcessedItem] = []
        stack: List[ProcessedItem] = []

        for definition in definitions:
            while len(stack) > definition.depth:
                to_close = stack.pop()
                self.release_pin(str(to_close.payload["id"]), to_close.path)

            current_depth = len(stack)
            if definition.depth > current_depth and definition.depth != current_depth + 1:
                raise LayoutParseError(
                    f"Line {definition.line_number} jumps from depth {current_depth} to {definition.depth}; please indent by one level at a time."
                )

            parent = stack[-1] if definition.depth > 0 else None
            if definition.depth > 0 and parent is None:
                raise LayoutParseError(
                    f"Line {definition.line_number} is indented but no parent item is available."
                )

            path = definition.cleaned_name if parent is None else f"{parent.path} > {definition.cleaned_name}"

            if parent is not None:
                parent_id = str(parent.payload["id"])
                self.ensure_pinned(parent_id, parent.path)
            else:
                parent_id = None

            inserted = self.create_item(definition, path)

            processed_entry = ProcessedItem(
                definition=definition,
                payload=inserted,
                path=path,
                parent_id=parent_id,
            )
            processed.append(processed_entry)
            stack.append(processed_entry)

            if parent_id is not None:
                child_id = str(inserted["id"])
                self._relationship_pairs.append((child_id, parent_id))

        while stack:
            to_close = stack.pop()
            self.release_pin(str(to_close.payload["id"]), to_close.path)

        return processed

    def finalize_sql(self) -> str:
        """Return the accumulated SQL script."""

        if self.mode != "sql":
            return ""

        if self._relationship_pairs:
            self._sql_lines.append("-- Containment relationships")
            for child_id, parent_id in self._relationship_pairs:
                child_literal = _sql_literal(str(child_id))
                parent_literal = _sql_literal(str(parent_id))
                update_stmt_lines = [
                    "UPDATE relationships",
                    f"SET assoc_type = (COALESCE(assoc_type, 0) | {CONTAINMENT_BIT})",
                    f"WHERE item_id = {child_literal} AND assoc_id = {parent_literal};",
                ]
                self._sql_lines.append("\n".join(update_stmt_lines))
                insert_stmt_lines = [
                    "INSERT INTO relationships (item_id, assoc_id, assoc_type)",
                    f"SELECT {child_literal}, {parent_literal}, {CONTAINMENT_BIT}",
                    "WHERE NOT EXISTS (",
                    "    SELECT 1 FROM relationships",
                    f"    WHERE item_id = {child_literal} AND assoc_id = {parent_literal}",
                    ");",
                ]
                self._sql_lines.append("\n".join(insert_stmt_lines))
        header = [
            "-- SQL script generated by starting_container_layout_importer.py",
            f"-- Generated at {self._utc_now_iso()} UTC",
            "SET search_path TO public;",
            "",
        ]
        return "\n".join(header + self._sql_lines)


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the argument parser used by :func:`main`."""

    parser = argparse.ArgumentParser(
        description=(
            "Import a tab-indented container layout, creating one item per line and establishing containment relationships."
        )
    )
    parser.add_argument(
        "layout_file",
        type=Path,
        help="Path to the indented layout text file.",
    )
    parser.add_argument(
        "--mode",
        choices=["execute", "dry-run", "sql"],
        default="dry-run",
        help=(
            "Select the execution mode: 'execute' performs the operations, 'dry-run' prints the planned calls, and 'sql' emits the equivalent SQL script."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Increase logging detail.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Script entry point."""

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    layout_path = args.layout_file
    if not layout_path.is_absolute():
        layout_path = (Path.cwd() / layout_path).resolve()

    try:
        definitions = parse_layout_file(layout_path)
    except LayoutParseError as exc:
        parser.error(str(exc))

    importer = LayoutImporter(args.mode)
    try:
        processed = importer.process(definitions)
    except LayoutParseError as exc:
        parser.error(str(exc))

    if args.mode == "sql":
        script_output = importer.finalize_sql()
        print(script_output)
    else:
        LOGGER.info("Created %d items.", len(processed))

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
