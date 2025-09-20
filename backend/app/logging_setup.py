# backend/app/logging_setup.py
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler

REPO_ROOT = Path(__file__).resolve().parents[2]

class DateSizeRotatingFileHandler(RotatingFileHandler):
    """
    Like RotatingFileHandler, but when size is exceeded it creates a NEW file
    whose name includes the current date/time (instead of .1, .2, ...).
    """

    def __init__(
        self,
        directory: Path,
        prefix: str = "app",
        max_bytes: int = 1_000_000,
        encoding: str = "utf-8",
        errors: str = "replace",
    ):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.maxBytes = max_bytes  # used by base class
        # initial filename
        filename = self._new_filename()
        # Some Python versions don't accept 'errors='; fall back gracefully
        try:
            super().__init__(filename, maxBytes=max_bytes, backupCount=0, encoding=encoding, errors=errors)
        except TypeError:
            super().__init__(filename, maxBytes=max_bytes, backupCount=0, encoding=encoding)

    def _new_filename(self) -> str:
        # Include milliseconds to avoid collisions if rolling multiple times in one second
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]  # e.g., 20250908-102530-123
        return str(self.directory / f"{self.prefix}-{ts}.log")

    def doRollover(self) -> None:
        """
        Open a brand-new file with a fresh timestamped name when size threshold is hit.
        """
        if self.stream:
            self.stream.close()
            self.stream = None
        # Point the handler at a fresh file
        new_name = self._new_filename()
        self.baseFilename = os.fspath(new_name)
        self.mode = "a"
        self.stream = self._open()


def _coerce_level(level: Optional[str | int]) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return getattr(logging, level.upper(), logging.INFO)
    env_level = os.getenv("LOG_LEVEL", "INFO")
    return getattr(logging, env_level.upper(), logging.INFO)


def start_log(
    *,
    app_name: str = "app",
    log_dir: Optional[str | Path] = None,
    level: Optional[str | int] = None,
    to_console: bool = True,
    max_bytes: int = 1_000_000,
) -> logging.Logger:
    """
    Initialize logging for the whole backend.

    - Creates repo_root/var/log by default (or LOG_DIR env) and writes UTF-8 logs
      so emojis 👍 won't crash it.
    - Rotates when ~max_bytes is reached by starting a NEW timestamped file.
    - Configures the ROOT logger so all modules using logging.getLogger(__name__)
      will write here after you call start_log() once.

    Returns the configured root logger.
    """
    # Default log directory:
    # 1) LOG_DIR env var, else
    # 2) <current working dir>/var/log (repo-root if you run from git dir)
    if log_dir is None:
        log_dir = os.getenv("LOG_DIR", None)
    if log_dir is None:
        log_dir = REPO_ROOT / "var" / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(_coerce_level(level))

    # Avoid duplicate handlers if start_log() gets called twice (e.g., dev reload)
    for h in list(root.handlers):
        root.removeHandler(h)

    # File handler (UTF-8, safe with emojis)
    file_handler = DateSizeRotatingFileHandler(
        directory=log_dir,
        prefix=app_name,
        max_bytes=max_bytes,
        encoding="utf-8",
        errors="replace",
    )
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Optional console (nice during dev)
    if to_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(root.level)
        root.addHandler(console)

    # Friendly startup line
    root.info("Logging started ▶️ app=%s dir=%s level=%s", app_name, str(log_dir), logging.getLevelName(root.level))
    return root

# not useful
def get_log(x: str = __name__):
    return logging.getLogger(x if x else __name__)
