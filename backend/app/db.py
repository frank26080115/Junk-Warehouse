# backend/apps/db.py
from __future__ import annotations

import json
import logging
import os
import threading
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union
from urllib.parse import quote_plus
import uuid as _uuid
from flask import Blueprint, g, has_app_context, jsonify
from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData, Table, select, text
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.exc import NoSuchTableError
import app.helpers as helpers


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def unwrap_db_result(result: Any) -> Tuple[int, bool, Dict[str, Any], Dict[str, Any], Any, str]:
    """Normalize responses into ``(status, is_error, reply, row, pk, message)`` tuples."""

    status_code: int
    reply_payload: Any
    if isinstance(result, tuple) and len(result) == 2:
        reply_payload, status_code = result
    else:
        reply_payload = result
        status_code = getattr(result, "status_code", 200)

    if isinstance(reply_payload, Mapping):
        reply_obj = dict(reply_payload)
    elif reply_payload is None:
        reply_obj = {}
    else:
        reply_obj = {"data": reply_payload}

    http_code = int(status_code or 0)
    ok_flag = reply_obj.get("ok")
    is_error = bool(http_code >= 400 or (ok_flag is False))

    data_section = reply_obj.get("data") if isinstance(reply_obj, dict) else None
    if isinstance(data_section, Mapping):
        row_dict = dict(data_section)
    else:
        row_dict = {}

    primary_key = row_dict.get("id")
    if primary_key is None:
        for key, value in row_dict.items():
            if isinstance(key, str) and key.lower().endswith("_id"):
                primary_key = value
                break

    message_fields = ("message", "msg", "error", "detail", "reason")
    message_text = ""
    for field in message_fields:
        value = reply_obj.get(field)
        if value:
            message_text = str(value)
            break
    if not message_text:
        message_text = "OK" if not is_error else f"database error (HTTP {http_code})"

    return http_code, is_error, reply_obj, row_dict, primary_key, message_text

log = logging.getLogger(__name__)

bp = Blueprint("dbstatus", __name__)

# Module-level singletons
_ENGINE: Optional[Engine] = None
_SESSION_FACTORY: Optional[sessionmaker] = None
_SESSION_LOCAL: Optional[scoped_session] = None
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
        log.debug("loading /backend/.env")
        load_dotenv(BACKEND_ENV, override=False)
    if ROOT_ENV.exists():
        log.debug("loading root/.env")
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
        log.debug(f"db cfg loaded json file {OPTIONAL_DB_JSON}")
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

    #log.debug("db cfg from .env:")
    #log.debug(cfg)

    # Fill any missing from optional JSON (non-secret)
    if any(v is None for v in cfg.values()):
        json_fallback = _from_db_json()
        for k in cfg:
            if cfg[k] is None and k in json_fallback:
                cfg[k] = json_fallback[k]

    #log.debug("db cfg after json:")
    #log.debug(cfg)

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
    global _SESSION_FACTORY
    global _SESSION_LOCAL
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

        session_factory = sessionmaker(bind=_ENGINE, future=True)
        _SESSION_FACTORY = session_factory
        _SESSION_LOCAL = scoped_session(session_factory)
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


def get_or_create_session() -> Session:
    """
    Return the current request's Session if it exists, otherwise
    create one from the scoped_session and attach it to g.
    """
    global _SESSION_LOCAL
    if _SESSION_LOCAL is None:
        #raise RuntimeError("DB not initialized; call init_engine() first")
        if not get_engine():
            raise RuntimeError("Unable to automatically initialize DB engine for DB session")

    s = getattr(g, "db", None)
    if s is None:
        s = _SESSION_LOCAL()      # returns current thread's Session (creates if absent)
        g.db = s                  # stash for the rest of this request
    return s


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a database session and guarantee the associated connection is released."""

    # When a Flask application context is active we reuse the request-scoped
    # session managed by :func:`get_or_create_session`.  Background jobs, CLI
    # utilities, and other callers that run outside of Flask receive a
    # temporary session that is explicitly closed once the block completes.

    created_here = False

    if has_app_context():
        session = get_or_create_session()
    else:
        global _SESSION_FACTORY

        factory = _SESSION_FACTORY
        if factory is None:
            engine = get_engine()
            factory = sessionmaker(bind=engine, future=True)
            _SESSION_FACTORY = factory

        session = factory()
        created_here = True

    try:
        yield session
    except Exception:
        if session.in_transaction():
            session.rollback()
        raise
    finally:
        if created_here:
            session.close()


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
    global _ENGINE, _SESSION_FACTORY, _SESSION_LOCAL

    if _ENGINE is not None:
        _ENGINE.dispose()
        _ENGINE = None

    if _SESSION_LOCAL:
        _SESSION_LOCAL.remove()
        _SESSION_LOCAL = None

    _SESSION_FACTORY = None


def db_cleanup(_exc):
    global _SESSION_LOCAL

    try:
        g.pop("db", None)
    except RuntimeError:
        # Outside an application context there is no ``g`` to mutate.
        pass

    if _SESSION_LOCAL:
        _SESSION_LOCAL.remove()


def get_db_item_as_dict(engine: Engine, table: str, uuid, id_col_name:str = "id"):
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

    stmt = select(target).where(target.c[id_col_name] == pk).limit(1)

    with Session(engine) as session:
        result = session.execute(stmt).mappings().first()  # mappings() gives dict-like rows

    if result is None:
        raise LookupError(f"No row found in {table!r} with id={pk}")

    return dict(result)


def update_db_row_by_dict(
    engine: Engine,
    table: str,
    uuid: Optional[Union[str, "uuid.UUID"]],
    data: Union[str, Mapping[str, Any], Any],
    fuzzy: bool = True,
    id_col_name: str = "id"
):
    """
    Insert or update a row in 'table' using a dict-like payload.
    - 'uuid' = "new" → INSERT; else UPDATE row whose primary key (or 'id') equals uuid.
    - If uuid is None and 'id' exists in data, uuid := data['id'].
    - If 'data' is a string, it's parsed as JSON first.
    - If 'fuzzy' is True, rename keys to best-matching column names when edit distance <= 2.
      One column can only be matched once; log mismatches at debug level.
    - Keys not present as columns are dropped (with debug logs). No sanitization done.
    - Returns (dict_response, http_code).
    """
    # 1) normalize data -> dict
    if isinstance(data, str):
        try:
            payload = json.loads(data)
            if not isinstance(payload, Mapping):
                return {"ok": False, "error": "JSON must be an object"}, 400
            payload = dict(payload)
        except Exception as e:
            log.debug("bad JSON payload: %s", e)
            return {"ok": False, "error": "Invalid JSON string"}, 400
    elif isinstance(data, Mapping):
        payload = dict(data)
    else:
        # last resort: try to cast to dict (e.g., ImmutableDict)
        try:
            payload = dict(data)  # type: ignore[call-arg]
        except Exception:
            return {"ok": False, "error": "Unsupported data type for 'data'"}, 400

    # 2) Pick up id from payload if uuid is None
    if uuid is None and id_col_name in payload:
        uuid = payload.get(id_col_name)

    # 3) reflect table & columns
    md = MetaData()
    try:
        t: Table = Table(table, md, autoload_with=engine)
    except Exception:
        return {"ok": False, "error": f"Unknown table '{table}'"}, 400

    column_names = [c.name for c in t.columns]
    columns_set = set(column_names)

    # 4) fuzzy remap keys (<=2 edits) before filtering
    if fuzzy:
        payload = helpers.fuzzy_apply_fuzzy_keys(payload, columns_set, table_name=table, limit=2)

    # 5) filter to known columns; log drops
    filtered: dict[str, Any] = {}
    for k, v in payload.items():
        if k in columns_set:
            filtered[k] = v
        else:
            log.debug("dropping unknown key '%s' (not a column of %s)", k, table)

    for key, value in list(filtered.items()):
        if isinstance(value, str):
            filtered[key] = value.strip()

    # 6) determine primary key column to target
    pk_cols = list(t.primary_key.columns)
    if len(pk_cols) == 1:
        pk_name = pk_cols[0].name
    else:
        # fallback preference: 'id' if present, else first PK col, else 'id'
        pk_name = id_col_name if id_col_name in columns_set else (pk_cols[0].name if pk_cols else id_col_name)

    # 7) Insert or update?
    is_insert = isinstance(uuid, str) and (uuid.lower() == "new" or uuid.lower() == "insert")

    if not is_insert and (uuid is None or (isinstance(uuid, str) and uuid.strip() == "")):
        return {"ok": False, "error": "uuid required for update (or pass 'new' to insert)"}, 400

    if isinstance(uuid, str) and not is_insert:
        uuid = helpers.normalize_pg_uuid(uuid)

    # Don’t allow updating the PK itself (keep where-clause the source of truth)
    if not is_insert and pk_name in filtered:
        if str(filtered[pk_name]) != str(uuid):
            log.debug("removing '%s' from update payload to avoid PK change", pk_name)
        filtered.pop(pk_name, None)

    # 8) execute with RETURNING * to get the row back
    with engine.begin() as conn:
        try:
            if is_insert:
                stmt = t.insert().values(**filtered).returning(t)
                row = conn.execute(stmt).mappings().first()
                if row is None:
                    return {"ok": False, "error": "insert failed (no row returned)"}, 400
                return {"ok": True, "data": dict(row)}, 201

            # UPDATE path
            pk_col = t.c.get(pk_name)  # type: ignore[attr-defined]
            if pk_col is None:
                return {"ok": False, "error": f"Primary key column '{pk_name}' not found"}, 400

            stmt = t.update().where(pk_col == uuid).values(**filtered).returning(t)
            row = conn.execute(stmt).mappings().first()
            if row is None:
                return {"ok": False, "error": "not found"}, 404
            return {"ok": True, "data": dict(row)}, 200
        except Exception as e:
            log.exception("DB write failed on table '%s'", table)
            # surfacing DB error text can be useful during dev; trim in prod if needed
            return {"ok": False, "error": "database error", "detail": str(e)}, 400


def get_column_types(engine: Engine, table: str) -> Dict[str, str]:
    """
    Return a mapping of column name -> SQL type (as string) for the given table.
    Supports 'schema.table' or just 'table'.

    Example:
        types = get_column_types(engine, "public.users")
        # -> {"id": "INTEGER", "email": "VARCHAR(255)", ...}
    """
    schema = None
    table_name = table
    if "." in table:
        schema, table_name = table.split(".", 1)

    md = MetaData()
    try:
        t = Table(table_name, md, schema=schema, autoload_with=engine)
    except NoSuchTableError:
        raise ValueError(f"Table not found: {table!r}") from None

    return {col.name: str(col.type) for col in t.columns}


def deduplicate_rows(
    rows: List[Dict[str, Any]],
    key: str = "id",
    keep: str = "first"
) -> List[Dict[str, Any]]:
    """
    Remove duplicate dict rows based on a given key.

    Parameters
    ----------
    rows : List[Dict[str, Any]]
        Input list of row dictionaries.
    key : str, default "id"
        Column name used as the primary key for uniqueness.
    keep : {"first", "last"}, default "first"
        Strategy for which duplicate to keep:
        - "first": preserve the first occurrence of each key.
        - "last": preserve the last occurrence of each key.

    Returns
    -------
    List[Dict[str, Any]]
        Deduplicated list of row dictionaries.
    """
    if keep not in {"first", "last"}:
        raise ValueError("keep must be either 'first' or 'last'")

    if keep == "first":
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for row in rows:
            if key not in row:
                deduped.append(row)
                continue
            value = row[key]
            if value not in seen:
                seen.add(value)
                deduped.append(row)
        return deduped

    else:  # keep == "last"
        # Build a mapping from key -> row (last one wins)
        mapping: Dict[Any, Dict[str, Any]] = {}
        with_no_key: List[Dict[str, Any]] = []
        for row in rows:
            if key not in row:
                with_no_key.append(row)
                continue
            mapping[row[key]] = row

        # Preserve order by re-walking input rows, but emit only final survivors
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for row in rows:
            if key not in row:
                continue  # already stored
            value = row[key]
            if value not in seen and mapping[value] is row:
                seen.add(value)
                deduped.append(row)

        # Rows missing the key are always included, order preserved
        deduped.extend(with_no_key)
        return deduped


@bp.get("/api/getdbqueuesize")
def get_database_queue_size():
    """Return a lightweight JSON payload describing the current database connection usage."""

    engine = get_engine()

    # SQLAlchemy's QueuePool exposes a descriptive status string. The text always contains the
    # "Current Checked out connections" figure, which is the most meaningful representation of the
    # queue size for administrators monitoring live load.
    status_text = engine.pool.status()

    match = re.search(r"Current Checked out connections:\s*(\d+)", status_text)
    if match:
        current_connections = int(match.group(1))
    else:
        digits = re.findall(r"\d+", status_text)
        current_connections = int(digits[-1]) if digits else 0

    # Provide both the parsed integer and the original status string so operators can inspect the
    # full context if they choose to surface the endpoint elsewhere.
    return jsonify({
        "ok": True,
        "queue_size": current_connections,
        "status": status_text,
    })
