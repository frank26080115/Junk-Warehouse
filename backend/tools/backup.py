from __future__ import annotations

"""Create filesystem and database backups for Junk Warehouse."""

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

CURRENT_FILE = Path(__file__).resolve()
REPO_ROOT = CURRENT_FILE.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import logging_setup
from backend.app.config_loader import get_private_dir_path
from backend.app.static_server import get_public_html_path
from backend.app.db import _build_db_url
from backend.app.history import log_history

DEFAULT_BACKUP_DIR = Path(r"C:\junkwarehouse\backups")
ZIP_DIR_NAME = "zips"
GITIGNORE_ZIPS_RULE = "zips/"
# Build canonical newline markers once so we can reuse them for gitignore updates.
CARRIAGE_RETURN = chr(13)
LINE_FEED = chr(10)
WINDOWS_EOL = CARRIAGE_RETURN + LINE_FEED
FOUR_GIB = 4 * 1024 * 1024 * 1024

log = logging.getLogger(__name__)


def ensure_logging_initialized() -> None:
    """Start the shared logging configuration when the caller has not already done so."""

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging_setup.start_log(app_name="backup", to_console=True)


def ensure_backup_repository(backup_root: Path) -> None:
    """Create the backup directory, initialize git, and ensure supporting folders exist."""

    backup_root.mkdir(parents=True, exist_ok=True)
    git_dir = backup_root / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], check=True, cwd=str(backup_root))
        log.info("Initialized backup git repository at %s", backup_root)
    ensure_gitignore_rules(backup_root)
    (backup_root / ZIP_DIR_NAME).mkdir(parents=True, exist_ok=True)


def ensure_gitignore_rules(backup_root: Path) -> None:
    """Guarantee that zips/ is ignored so large archives do not enter version control."""

    gitignore_path = backup_root / ".gitignore"
    desired_line = GITIGNORE_ZIPS_RULE
    if gitignore_path.exists():
        try:
            raw_text = gitignore_path.read_text(encoding="utf-8")
        except Exception as exc:
            log.warning("Unable to read existing .gitignore at %s: %s", gitignore_path, exc)
            return
        if desired_line in raw_text.splitlines():
            return
        new_text = raw_text.rstrip(CARRIAGE_RETURN + LINE_FEED) + WINDOWS_EOL + desired_line + WINDOWS_EOL
    else:
        new_text = desired_line + WINDOWS_EOL
    try:
        gitignore_path.write_text(new_text, encoding="utf-8", newline=WINDOWS_EOL)
    except TypeError:
        gitignore_path.write_text(new_text, encoding="utf-8")


def _should_skip_relative(relative_path: Path, ignored_roots: Optional[Set[str]]) -> bool:
    if not ignored_roots:
        return False
    if not relative_path.parts:
        return False
    return relative_path.parts[0] in ignored_roots


def mirror_directory(source: Path, destination: Path, ignored_roots: Optional[Set[str]] = None) -> None:
    """Copy source into destination while preserving timestamps and skipping ignored folders."""

    if not source.exists():
        log.warning("Source directory %s does not exist, skipping copy.", source)
        return
    for root_dir, dir_names, file_names in os.walk(source):
        root_path = Path(root_dir)
        relative_root = root_path.relative_to(source)
        if _should_skip_relative(relative_root, ignored_roots):
            dir_names[:] = []
            continue
        filtered_dirs: List[str] = []
        for dir_name in dir_names:
            candidate_relative = (root_path / dir_name).relative_to(source)
            if _should_skip_relative(candidate_relative, ignored_roots):
                continue
            filtered_dirs.append(dir_name)
        dir_names[:] = filtered_dirs
        target_root = destination / relative_root
        target_root.mkdir(parents=True, exist_ok=True)
        for file_name in file_names:
            candidate_relative = relative_root / file_name
            if _should_skip_relative(candidate_relative, ignored_roots):
                continue
            source_file = root_path / file_name
            destination_file = target_root / file_name
            shutil.copy2(str(source_file), str(destination_file))


def run_pg_dumps(database_dir: Path) -> None:
    """Execute pg_dump twice: once for the schema and once for the full database."""

    database_dir.mkdir(parents=True, exist_ok=True)
    try:
        db_url = _build_db_url()
    except Exception as exc:
        log.exception("Could not resolve database URL: %s", exc)
        raise
    schema_path = database_dir / "schema.sql"
    dump_path = database_dir / "database.dump"
    log.info("Running pg_dump for schema to %s", schema_path)
    with open(schema_path, "w", encoding="utf-8", newline=LINE_FEED) as schema_file:
        subprocess.run(
            ["pg_dump", "--schema-only", "--no-owner", "--no-privileges", db_url],
            check=True,
            stdout=schema_file,
        )
    log.info("Running pg_dump for full database to %s", dump_path)
    with open(dump_path, "wb") as dump_file:
        subprocess.run(
            ["pg_dump", "--format=custom", "--compress=0", "--no-owner", "--no-privileges", db_url],
            check=True,
            stdout=dump_file,
        )


def git_status_has_changes(backup_root: Path, relative_item: Path) -> bool:
    """Return True when git reports modifications for the requested path."""

    result = subprocess.run(
        ["git", "-C", str(backup_root), "status", "--porcelain", str(relative_item)],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def create_zip_archive(source: Path, archive_path: Path, ignored_roots: Optional[Set[str]] = None) -> None:
    """Write a timestamped ZIP archive for the directory, honoring ignore rules."""

    import zipfile

    with zipfile.ZipFile(str(archive_path), mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in source.rglob("*"):
            if file_path.is_dir():
                continue
            relative_path = file_path.relative_to(source)
            if _should_skip_relative(relative_path, ignored_roots):
                continue
            archive.write(str(file_path), str(relative_path))
    log.info("Created archive %s", archive_path)


def compute_directory_size_bytes(target: Path) -> int:
    """Calculate the total size for the entire backup directory tree."""

    total = 0
    for entry in target.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except FileNotFoundError:
            continue
    return total


def run_backup(backup_location: Optional[Path | str] = None) -> Dict[str, bool]:
    """Perform the full backup workflow and return a mapping of changed directories."""

    ensure_logging_initialized()
    target_dir = Path(backup_location) if backup_location is not None else DEFAULT_BACKUP_DIR
    target_dir = target_dir.expanduser().resolve()
    log.info("Starting backup into %s", target_dir)
    ensure_backup_repository(target_dir)

    repo_config_dir = REPO_ROOT / "config"
    private_source_raw = get_private_dir_path()
    public_source_raw = get_public_html_path()

    changed_flags: Dict[str, bool] = {"config": False, "private": False, "public_html": False, "database": False}

    mirror_directory(repo_config_dir, target_dir / "config")
    changed_flags["config"] = git_status_has_changes(target_dir, Path("config"))

    if private_source_raw:
        private_source = Path(private_source_raw)
        mirror_directory(private_source, target_dir / "private", {"tmp"})
        changed_flags["private"] = git_status_has_changes(target_dir, Path("private"))
    else:
        log.warning("Private directory path is not configured; skipping private backup.")

    if public_source_raw:
        public_source = Path(public_source_raw)
        mirror_directory(public_source, target_dir / "public_html", {"tmp"})
        changed_flags["public_html"] = git_status_has_changes(target_dir, Path("public_html"))
    else:
        log.warning("Public HTML directory path is not configured; skipping public_html backup.")

    run_pg_dumps(target_dir / "database")
    changed_flags["database"] = git_status_has_changes(target_dir, Path("database"))

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_dir = target_dir / ZIP_DIR_NAME
    zip_labels = {"config": "backup-config", "database": "backup-database", "private": "backup-private", "public_html": "backup-public_html"}
    ignore_map = {"config": None, "database": None, "private": {"tmp"}, "public_html": {"tmp"}}

    # Create zipped snapshots only for directories with detected changes.
    for key, changed in changed_flags.items():
        if not changed:
            continue
        source_folder = target_dir / key
        archive_name = f"{zip_labels[key]}-{timestamp}.zip"
        archive_path = zip_dir / archive_name
        create_zip_archive(source_folder, archive_path, ignore_map.get(key))

    subprocess.run(["git", "-C", str(target_dir), "add", "-A"], check=True)
    status_result = subprocess.run(
        ["git", "-C", str(target_dir), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    if status_result.stdout.strip():
        commit_message = datetime.now().strftime("Backup %Y-%m-%d %H:%M:%S")
        subprocess.run(["git", "-C", str(target_dir), "commit", "-m", commit_message], check=True)
        log.info("Committed backup snapshot: %s", commit_message)
    else:
        subprocess.run(["git", "-C", str(target_dir), "reset", "HEAD"], check=True)
        log.info("No changes detected; nothing was committed.")

    changed_names = [name for name, flag in changed_flags.items() if flag]
    meta_value = ",".join(changed_names) if changed_names else "none"
    try:
        log_history(event="backup", meta=meta_value)
    except Exception as exc:
        log.exception("Failed to log backup history event: %s", exc)

    total_size = compute_directory_size_bytes(target_dir)
    if total_size > FOUR_GIB:
        log.warning("WARNING: backup directory %s exceeds 4 GiB (size=%s bytes)", target_dir, total_size)

    return changed_flags


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for command-line usage."""

    args = list(argv if argv is not None else sys.argv[1:])
    backup_arg: Optional[Path | str] = args[0] if args else None
    try:
        run_backup(backup_arg)
    except Exception:
        log.exception("Backup process failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

