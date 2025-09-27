#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def get_repo_files() -> list[Path]:
    result = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        text=True,
    )
    return [Path(line) for line in result.splitlines() if line]


def normalize_to_crlf(path: Path) -> bool:
    data = path.read_bytes()
    if b"\0" in data or b"\r\n" not in data:
        return False

    # When a file contains multiple carriage return characters before a newline,
    # such as "\r\r\n", we collapse that sequence down to a single carriage
    # return to avoid creating duplicate blank lines after normalization.
    cleaned = re.sub(rb"\r+\n", b"\r\n", data)

    normalized = cleaned.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    converted = normalized.replace(b"\n", b"\r\n")

    if converted != data:
        path.write_bytes(converted)
        return True

    return False


def main() -> None:
    repo_root = Path.cwd()
    changed: list[Path] = []

    for rel_path in get_repo_files():
        file_path = repo_root / rel_path
        if not file_path.is_file():
            continue

        try:
            updated = normalize_to_crlf(file_path)
        except OSError as exc:
            print(f"Skipping {rel_path}: {exc}")
            continue

        if updated:
            changed.append(rel_path)

    if changed:
        print("Updated line endings for:")
        for path in changed:
            print(f" - {path}")
    else:
        print("No files required updates.")


if __name__ == "__main__":
    main()
