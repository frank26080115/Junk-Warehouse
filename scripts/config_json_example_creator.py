
"""Example configuration JSON creator.

This script generates shareable ".example" configuration files from real configuration JSON
files. It preserves the structure of the configuration while obfuscating potentially sensitive
string values so that the sanitized examples can be committed to public repositories.
"""

import json
import random
import string
from pathlib import Path
from typing import Any


LOWER_HEX = "abcdef"
UPPER_HEX = "ABCDEF"


def transform_string(value: str) -> str:
    """Return an obfuscated version of ``value`` with matching structure and length."""
    characters = []
    for ch in value:
        if "a" <= ch <= "f":
            characters.append(random.choice(LOWER_HEX))
        elif "A" <= ch <= "F":
            characters.append(random.choice(UPPER_HEX))
        elif ch.islower() and ch >= "e":
            characters.append(random.choice(string.ascii_lowercase))
        elif ch.isupper() and ch >= "E":
            characters.append(random.choice(string.ascii_uppercase))
        elif ch.isdigit():
            characters.append(random.choice(string.digits))
        else:
            characters.append(ch)
    return "".join(characters)


def transform_value(value: Any) -> Any:
    """Obfuscate string content while leaving other data types untouched."""
    if isinstance(value, str):
        return transform_string(value)
    if isinstance(value, list):
        return [transform_value(item) for item in value]
    if isinstance(value, dict):
        return {key: transform_value(val) for key, val in value.items()}
    return value


def merge_example_content(existing: Any, updated: Any) -> Any:
    """Merge ``updated`` into ``existing`` without deleting keys from the example file."""
    if isinstance(updated, dict):
        merged = {}
        if isinstance(existing, dict):
            merged.update(existing)
        for key, updated_value in updated.items():
            existing_value = existing.get(key) if isinstance(existing, dict) else None
            merged[key] = merge_example_content(existing_value, updated_value)
        return merged
    if isinstance(updated, str):
        if isinstance(existing, str) and len(existing) == len(updated):
            return existing
        return updated
    return updated if updated is not None else existing


def process_file(json_path: Path) -> None:
    """Create or update the example file for ``json_path`` with obfuscated content."""
    with json_path.open("r", encoding="utf-8") as source:
        data = json.load(source)

    transformed = transform_value(data)
    example_path = json_path.with_suffix(json_path.suffix + ".example")
    existing_example = None
    if example_path.exists():
        with example_path.open("r", encoding="utf-8") as existing_file:
            existing_example = json.load(existing_file)

    merged = merge_example_content(existing_example, transformed)
    with example_path.open("w", encoding="utf-8") as target:
        json.dump(merged, target, ensure_ascii=False, indent=2)
        target.write("\n")


def main() -> None:
    """Process every JSON configuration file within the ``config`` directory tree."""
    config_dir = Path("config")
    if not config_dir.is_dir():
        raise SystemExit("config directory not found")

    for json_path in sorted(config_dir.rglob("*.json")):
        process_file(json_path)


if __name__ == "__main__":
    main()
