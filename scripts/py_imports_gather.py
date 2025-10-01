"""Utility script to gather Python import statements across the project."""

import os
import re
from pathlib import Path
from typing import Iterable, List, Set

# Regular expression designed to capture both simple 'import' statements and
# 'from ... import ...' statements. The pattern operates in MULTILINE mode so
# that caret (^) and dollar sign ($) anchors respect individual lines.
IMPORT_PATTERN = re.compile(r'^\s*(?:from\s+\S+\s+import\s+.+|import\s+.+)$', re.MULTILINE)


def remove_trailing_comment(raw_line: str) -> str:
    """Return the line content without any trailing comment text.

    The scanning logic keeps track of quoted strings so that hash symbols that
    appear inside string literals are preserved. This keeps legitimate module
    names such as `module#part` intact while still trimming genuine comments.
    """
    in_single_quote = False
    in_double_quote = False
    escaped = False
    for index, character in enumerate(raw_line):
        if escaped:
            escaped = False
            continue
        if character == '\\':
            escaped = True
            continue
        if character == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if character == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
        if character == '#' and not in_single_quote and not in_double_quote:
            return raw_line[:index].rstrip()
    return raw_line.rstrip()


def sanitize_import_statement(raw_statement: str) -> str:
    """Normalize an import statement by trimming whitespace and comments.

    The script prints sanitized statements so downstream tooling never sees
    inline comments or incidental spacing differences.
    """
    without_comment = remove_trailing_comment(raw_statement)
    return without_comment.strip()


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
    extracted_statements: List[str] = []
    for match in IMPORT_PATTERN.finditer(file_contents):
        sanitized = sanitize_import_statement(match.group(0))
        if sanitized:
            extracted_statements.append(sanitized)
    return extracted_statements


def format_import_output(python_file: Path, import_statements: Iterable[str]) -> List[str]:
    """Prepare formatted strings that pair file paths with their import statements."""
    formatted_results: List[str] = []
    relative_path = python_file.relative_to(Path.cwd())
    for statement in import_statements:
        formatted_results.append(f"{relative_path}: {statement}")
    return formatted_results


def parse_import_targets(import_statement: str) -> List[str]:
    """Return every module path referenced by a single import statement."""
    cleaned_statement = sanitize_import_statement(import_statement)
    if not cleaned_statement:
        return []
    targets: List[str] = []
    if cleaned_statement.startswith('from '):
        # Extract the module portion that follows the "from" keyword and
        # precedes the "import" keyword. Relative imports (those beginning
        # with a period) are ignored because they refer to local modules.
        source_details = cleaned_statement[5:].split(' import ', 1)
        if len(source_details) == 2:
            module_path = source_details[0].strip()
            if module_path and not module_path.startswith('.'):
                module_path = module_path.split(' ', 1)[0]
                targets.append(module_path)
        return targets
    if cleaned_statement.startswith('import '):
        # Split multiple imports separated by commas, respecting any aliases
        # by capturing only the portion before the "as" keyword.
        imported_modules = cleaned_statement[7:]
        for module_fragment in imported_modules.split(','):
            leading_module = module_fragment.strip().split(' as ', 1)[0].strip()
            if leading_module:
                leading_module = leading_module.split(' ', 1)[0]
                if not leading_module.startswith('.'):
                    targets.append(leading_module)
    return targets


def derive_library_name(import_target: str) -> str:
    """Return the high-level library name from a fully qualified import target.

    The resulting name always stops at the first space or period so it matches
    the user's expectation for a concise identifier.
    """
    cleaned_target = import_target.strip()
    if not cleaned_target:
        return ''
    primary_token = cleaned_target.split(' ', 1)[0]
    high_level = primary_token.split('.', 1)[0]
    return high_level


def collect_high_level_library_names(formatted_results: Iterable[str], backend_root: Path) -> List[str]:
    """Create a sorted list of unique top-level libraries from formatted import data."""
    backend_directories: Set[str] = set()
    if backend_root.exists():
        for backend_child in backend_root.iterdir():
            if backend_child.is_dir():
                backend_directories.add(backend_child.name)

    discovered_libraries: Set[str] = set()
    for result_line in formatted_results:
        try:
            _, import_statement = result_line.split(': ', 1)
        except ValueError:
            # If the line cannot be split, skip it while keeping the script resilient.
            continue
        for target in parse_import_targets(import_statement):
            library_name = derive_library_name(target)
            if not library_name:
                continue
            if library_name in backend_directories:
                continue
            discovered_libraries.add(library_name)

    return sorted(discovered_libraries)


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

    # After enumerating each import statement, present the unique high-level
    # libraries referenced throughout the codebase to support quick auditing.
    library_names = collect_high_level_library_names(all_results, project_root / 'backend')
    if library_names:
        print()
        print('High level imported libraries (excluding backend directories):')
        for library_name in library_names:
            print(library_name)


if __name__ == '__main__':
    # The script is intended to be executed from the repository root so that
    # Path.cwd() reflects the desired search boundary.
    main()
