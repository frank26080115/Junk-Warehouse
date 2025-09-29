
"""Helper utilities to make running backend scripts from the command line straightforward."""

from pathlib import Path
import os
import sys
from typing import Iterable, Optional, Union

PathInput = Union[str, os.PathLike[str]]

def _unique_paths(paths: Iterable[Path]) -> Iterable[Path]:
    """Yield each path only once while preserving the original order."""
    seen = set()
    for path in paths:
        normalized = path.resolve()
        if normalized not in seen:
            seen.add(normalized)
            yield normalized

def _resolve_script_directory(script_location: Optional[PathInput]) -> Path:
    """Resolve the directory that should represent the caller's working context."""
    if script_location is None:
        return Path.cwd().resolve()
    script_path = Path(script_location).resolve()
    return script_path if script_path.is_dir() else script_path.parent

def bootstrap(script_location: Optional[PathInput] = None, *, prepend: bool = True) -> Path:
    """Ensure repository paths are present in ``sys.path`` for direct script execution.

    Parameters
    ----------
    script_location:
        Pass ``__file__`` from the calling script to make its directory importable.
        When the script is executed from the command line, Python only includes the
        script's folder in ``sys.path``. This helper adds the project root and the
        ``backend`` package itself so imports succeed without manual tweaks.
    prepend:
        When ``True`` the discovered paths are inserted at the beginning of ``sys.path``.
        This keeps local modules ahead of globally installed packages, which is often
        desirable for command line utilities. Set to ``False`` if you prefer the
        entries to be appended instead.

    Returns
    -------
    Path
        The resolved repository root directory. Returning the path makes it simple
        for callers to perform additional path calculations when necessary.
    """
    backend_directory = Path(__file__).resolve().parent
    repository_root = backend_directory.parent
    script_directory = _resolve_script_directory(script_location)

    candidate_paths = [
        repository_root,
        backend_directory,
        script_directory,
    ]

    for candidate in _unique_paths(candidate_paths):
        candidate_text = str(candidate)
        if candidate_text in sys.path:
            continue
        if prepend:
            sys.path.insert(0, candidate_text)
        else:
            sys.path.append(candidate_text)

    return repository_root

__all__ = ["bootstrap"]

"""Usage Example
-----------------
Add the following near the top of any standalone script inside the ``backend`` directory::

    from backend import bootstrap
    bootstrap(__file__)

This will allow relative imports such as ``from backend.services import payments`` to
work even when the script is executed directly via ``python backend/tools/do_task.py``.
"""
