"""Utility script to gather Python import statements across the project."""

import os
import re
from pathlib import Path
from typing import Iterable, List, Tuple

# Regular expression designed to capture both simple 'import' statements and
# 'from ... import ...' statements. The pattern operates in MULTILINE mode so
# that caret (^) and dollar sign ($) anchors respect individual lines.
IMPORT_PATTERN = re.compile(r'^\s*(?:from\s+\S+\s+import\s+.+|import\s+.+)$', re.MULTILINE)

def discover_python_files(root: Path) -> Iterable[Path]:
    """Yield every Python source file beneath the provided root directory."""
    for current_root, directories, files in os.walk(root):
        # Sorting directory and file names ensures deterministic traversal
        # which keeps the printed output stable across runs.
        directories.sort()
        files.sort()
        current_path = Path(current_root)
        for filename in files:
            if filename.endswith('.py'):
                yield current_path / filename

def extract_imports(python_file: Path) -> List[str]:
    """Read a Python file and return all import statements detected via regex."""
    # Using 'errors="ignore"' ensures that even files with unexpected encodings
    # do not interrupt the overall search process.
    file_contents = python_file.read_text(encoding='utf-8', errors='ignore')
    return [match.group(0).strip() for match in IMPORT_PATTERN.finditer(file_contents)]

def format_import_output(python_file: Path, import_statements: Iterable[str]) -> List[str]:
    """Prepare formatted strings that pair file paths with their import statements."""
    formatted_results: List[str] = []
    relative_path = python_file.relative_to(Path.cwd())
    for statement in import_statements:
        formatted_results.append(f"{relative_path}: {statement}")
    return formatted_results

def main() -> None:
    """Locate and print every import statement discovered in project Python files."""
    project_root = Path.cwd()
    all_results: List[str] = []
    for python_file in discover_python_files(project_root):
        imports = extract_imports(python_file)
        if imports:
            all_results.extend(format_import_output(python_file, imports))
    # Presenting the information in sorted order groups identical statements together
    # and makes downstream consumption or diffing more predictable.
    for line in sorted(all_results):
        print(line)

if __name__ == '__main__':
    # The script is intended to be executed from the repository root so that
    # Path.cwd() reflects the desired search boundary.
    main()
