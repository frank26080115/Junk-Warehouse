#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

heading() {
  printf '\n\033[1m==>\033[0m %s\n' "$*"
}

has_command() {
  command -v "$1" >/dev/null 2>&1
}

invoke_backend_tool() {
  local description="$1"
  shift
  local tool="$1"
  shift

  if [[ ! -d "${REPO_ROOT}/backend" ]]; then
    return 0
  fi

  if [[ -f "${REPO_ROOT}/backend/pyproject.toml" ]] && has_command poetry; then
    heading "$description"
    (cd "${REPO_ROOT}/backend" && poetry run "$tool" "$@")
    return $?
  fi

  local venv_tool="${REPO_ROOT}/.venv/bin/${tool}"
  if [[ -x "${venv_tool}" ]]; then
    heading "$description"
    (cd "${REPO_ROOT}/backend" && "${venv_tool}" "$@")
    return $?
  fi

  if has_command "$tool"; then
    heading "$description"
    (cd "${REPO_ROOT}/backend" && "$tool" "$@")
    return $?
  fi

  return 127
}

run_backend_linters() {
  if [[ ! -d "${REPO_ROOT}/backend" ]]; then
    echo "Skipping backend linters: backend directory not found." >&2
    return 0
  fi

  if invoke_backend_tool "Backend: ruff" ruff check .; then
    :
  else
    local rc=$?
    if [[ ${rc} -eq 127 ]]; then
      echo "Skipping backend ruff check: ruff is not installed. Install via Poetry or pip to enable this step." >&2
    else
      return "${rc}"
    fi
  fi

  if invoke_backend_tool "Backend: black" black --check .; then
    :
  else
    local rc=$?
    if [[ ${rc} -eq 127 ]]; then
      echo "Skipping backend black check: black is not installed. Install via Poetry or pip to enable this step." >&2
    else
      return "${rc}"
    fi
  fi
}

check_line_endings() {
  heading "Checking line endings"

  local python_bin=""
  if has_command python3; then
    python_bin="$(command -v python3)"
  elif has_command python; then
    python_bin="$(command -v python)"
  else
    echo "Python interpreter not found; cannot validate line endings." >&2
    return 1
  fi

  REPO_ROOT="${REPO_ROOT}" "${python_bin}" <<'PY'
import os
import subprocess
import sys
from pathlib import Path

repo_root = Path(os.environ["REPO_ROOT"])

unix_suffixes = {".sh", ".bash", ".zsh", ".bats"}
unix_filenames = {"Makefile"}

def is_unix_only(path: Path) -> bool:
    if path.suffix.lower() in unix_suffixes:
        return True
    if path.name in unix_filenames:
        return True
    try:
        with path.open("rb") as handle:
            start = handle.read(2)
    except OSError:
        return False
    return start.startswith(b"#!")

bad = []
result = subprocess.run(
    ["git", "ls-files", "-z"],
    cwd=repo_root,
    check=True,
    stdout=subprocess.PIPE,
)
for raw in result.stdout.split(b"\0"):
    if not raw:
        continue
    relative = raw.decode("utf-8", "surrogateescape")
    path = repo_root / relative
    if not path.is_file():
        continue

    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"Warning: could not read {relative}: {exc}", file=sys.stderr)
        continue

    if b"\0" in data:
        continue
    if is_unix_only(path):
        continue
    if not data:
        continue
    if b"\n" not in data and b"\r" not in data:
        continue
    if b"\r\n" not in data:
        bad.append(relative)
        continue

    stripped = data.replace(b"\r\n", b"")
    if b"\n" in stripped or b"\r" in stripped:
        bad.append(relative)

if bad:
    print("The following files must use Windows-style (CRLF) line endings:")
    for item in bad:
        print(f"  {item}")
    sys.exit(1)

print("Line ending check passed.")
PY
}

run_backend_linters
check_line_endings
