# backend/apps/db.py
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus
import uuid as _uuid

from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData, Table, select, text
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.orm import Session

import helpers

log = logging.getLogger(__name__)

# Module-level singletons
_ENGINE: Optional[Engine] = None
_INIT_LOCK = threading.Lock()

# Resolve paths based on this file's location:
#   repo_root/backend/apps/db.py  -> parents[2] == repo_root
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
BACKEND_ENV = BACKEND_DIR / ".env"
ROOT_ENV = REPO_ROOT / ".env"          # used by docker compose (safe to load too)
OPTIONAL_DB_JSON = REPO_ROOT / "config" / "db.json"  # optional, if you want


def _load_env_once() -> None:
    """Load env files if present. Safe to call multiple times."""
    # Load backend/.env first (app runtime), then root .env (compose), without overwriting existing env
    if BACKEND_ENV.exists():
        load_dotenv(BACKEND_ENV, override=False)
    if ROOT_ENV.exists():
        load_dotenv(ROOT_ENV, override=False)


def _from_db_json() -> dict[str, str]:
    """
    Optional: read config/db.json (non-secret) for connection parts if envs are missing.
    File format example:
        {
          "DB_USER": "app",
          "DB_PASSWORD": "app",
          "DB_NAME": "app",
          "DB_HOST": "127.0.0.1",
          "DB_PORT": 5432
        }
    """
    if not OPTIONAL_DB_JSON.exists():
        return {}
    try:
        data = json.loads(OPTIONAL_DB_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # normalize keys to str
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        log.warning("Failed to read %s", OPTIONAL_DB_JSON, exc_info=True)
        return {}


def _build_db_url() -> str:
    """
    Decide the effective DATABASE_URL.
    Precedence:
      1) DATABASE_URL
      2) DB_* envs (or PG*), possibly backed by config/db.json
    """
    _load_env_once()

    # 1) Direct URL
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    # 2) Assemble from parts
    cfg = {
        # First take env vars (DB_* preferred, fall back to standard PG* names)
        "DB_USER": os.getenv("DB_USER") or os.getenv("PGUSER"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD") or os.getenv("PGPASSWORD"),
        "DB_NAME": os.getenv("DB_NAME") or os.getenv("PGDATABASE"),
        "DB_HOST": os.getenv("DB_HOST") or os.getenv("PGHOST") or "127.0.0.1",
        "DB_PORT": os.getenv("DB_PORT") or os.getenv("PGPORT") or "5432",
    }

    # Fill any missing from optional JSON (non-secret)
    if any(v is None for v in cfg.values()):
        json_fallback = _from_db_json()
        for k in cfg:
            if cfg[k] is None and k in json_fallback:
                cfg[k] = json_fallback[k]

    # Final sanity / defaults
    user = cfg["DB_USER"] or "app"
    pwd = cfg["DB_PASSWORD"] or "app"
    name = cfg["DB_NAME"] or "app"
    host = cfg["DB_HOST"] or "127.0.0.1"
    port = str(cfg["DB_PORT"] or "5432")

    # URL-encode password in case it has special chars
    safe_pwd = quote_plus(pwd)

    # Use SQLAlchemy 2.x psycopg (v3) driver
    return f"postgresql+psycopg://{user}:{safe_pwd}@{host}:{port}/{name}"


def get_engine() -> Engine:
    """
    Return a process-wide SQLAlchemy Engine (with pooling).
    Creates it on first use, thread-safe.
    """
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    with _INIT_LOCK:
        if _ENGINE is not None:
            return _ENGINE

        db_url = _build_db_url()

        # Optional tuning from env (with sensible defaults)
        echo = bool(int(os.getenv("SQLALCHEMY_ECHO", "0")))
        pool_size = int(os.getenv("SQLALCHEMY_POOL_SIZE", "5"))
        max_overflow = int(os.getenv("SQLALCHEMY_MAX_OVERFLOW", "10"))
        pool_pre_ping = bool(int(os.getenv("SQLALCHEMY_POOL_PRE_PING", "1")))

        log.info("Creating DB engine url=%s echo=%s pool_size=%s max_overflow=%s pre_ping=%s",
                 db_url, echo, pool_size, max_overflow, pool_pre_ping)

        _ENGINE = create_engine(
            db_url,
            echo=echo,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            future=True,  # explicit for 2.x style
        )
        return _ENGINE


def get_db_conn() -> Connection:
    """
    Get a SQLAlchemy Connection from the global Engine.
    Caller is responsible for closing it (use 'with' for convenience):

        from apps.db import get_db_conn
        with get_db_conn() as conn:
            rows = conn.execute(text("select 1")).all()
    """
    return get_engine().connect()


def ping_db() -> bool:
    """Quick health check."""
    try:
        with get_db_conn() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        log.exception("DB ping failed")
        return False


def dispose_engine() -> None:
    """Close all pooled connections (useful in tests or graceful shutdown)."""
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
        _ENGINE = None


def get_db_item_as_dict(engine: Engine, table: str, uuid):
    """
    Fetch a single row by UUID from `table` using SQLAlchemy and return it as a dict.

    Args:
        engine: a SQLAlchemy Engine instance (try using `get_engine()`)
        table:  table name, optionally schema-qualified (e.g. "public.items")
        uuid:   a str or uuid.UUID representing the row's primary key

    Returns:
        dict of column_name -> python_value

    Raises:
        ValueError   if the uuid is invalid
        LookupError  if no row is found
    """
    # validate uuid
    try:
        pk = uuid if isinstance(uuid, _uuid.UUID) else _uuid.UUID(str(helpers.normalize_pg_uuid(uuid)))
    except Exception as e:
        raise ValueError(f"Invalid UUID: {uuid!r}") from e

    # split schema.table if needed
    if "." in table:
        schema, tbl = table.split(".", 1)
    else:
        schema, tbl = None, table

    metadata = MetaData()
    target = Table(tbl, metadata, autoload_with=engine, schema=schema)

    stmt = select(target).where(target.c.id == pk).limit(1)

    with Session(engine) as session:
        result = session.execute(stmt).mappings().first()  # mappings() gives dict-like rows

    if result is None:
        raise LookupError(f"No row found in {table!r} with id={pk}")

    return dict(result)
