Here are three super-quick ways to see your Postgres table columns. Pick whichever fits your mood:

# 1) psql cheat-sheet (fastest)

From your repo root (adjust creds as needed):

```bash
# All tables in public schema
docker compose exec db psql -U app -d app -c "\dt public.*"

# Describe one table (columns, types, defaults, nullability, indexes)
docker compose exec db psql -U app -d app -c "\d+ public.users"

# Pretty, expanded view (great for wide tables)
docker compose exec db psql -U app -d app -c "\x on \d+ public.users \x off"
```

# 2) Pure SQL (no meta-commands)

List columns for one table:

```sql
SELECT
  column_name,
  data_type,
  is_nullable,
  column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users'
ORDER BY ordinal_position;
```

List every user table + columns (skip system schemas):

```sql
SELECT table_schema, table_name, ordinal_position, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog','information_schema')
ORDER BY table_schema, table_name, ordinal_position;
```

# 3) Tiny Python helper (lives in your repo)

Drop this at `backend/tools/db_describe.py` and run it from the **repo root** or `backend/`. It uses your `backend/.env` (`DATABASE_URL`).

```python
# backend/tools/db_describe.py
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy import inspect as sa_inspect

def load_db_url() -> str:
    # Load backend/.env relative to this file
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / "backend" / ".env", override=False)
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set (expected in backend/.env)")
    return url

def connect() -> Engine:
    return create_engine(load_db_url(), future=True, pool_pre_ping=True)

def describe_table(engine: Engine, full: str, default_schema="public") -> None:
    if "." in full:
        schema, table = full.split(".", 1)
    else:
        schema, table = default_schema, full
    insp = sa_inspect(engine)
    if table not in insp.get_table_names(schema=schema):
        # fall back to views too
        if table not in insp.get_view_names(schema=schema):
            raise SystemExit(f"Not found: {schema}.{table}")
    cols = insp.get_columns(table, schema=schema)
    print(f"\n{schema}.{table}")
    print("-" * (len(schema) + len(table) + 1))
    # header
    print(f"{'ord':>3}  {'column':<32} {'type':<24} {'nullable':<8} {'default'}")
    for c in cols:
        ordpos = c.get("ordinal_position", "?")
        name = c["name"]
        ctype = str(c["type"])
        nullable = "YES" if c.get("nullable", True) else "NO"
        default = c.get("default", "")
        print(f"{ordpos:>3}  {name:<32} {ctype:<24} {nullable:<8} {default}")
    # indexes (optional)
    with engine.connect() as conn:
        q = text("""
        SELECT i.relname AS index_name,
               pg_get_indexdef(ix.indexrelid) AS definition
        FROM pg_index ix
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = :schema AND t.relname = :table
        ORDER BY i.relname;
        """)
        rows = conn.execute(q, {"schema": schema, "table": table}).all()
        if rows:
            print("\nindexes:")
            for r in rows:
                print(f"  - {r.index_name}: {r.definition}")

def list_tables(engine: Engine, schema: str) -> None:
    insp = sa_inspect(engine)
    tables = insp.get_table_names(schema=schema)
    views = insp.get_view_names(schema=schema)
    print(f"\nSchema: {schema}")
    print("tables:")
    for t in tables: print(f"  - {t}")
    if views:
        print("views:")
        for v in views: print(f"  - {v}")

def main():
    ap = argparse.ArgumentParser(description="Describe Postgres tables/columns")
    ap.add_argument("--schema", default="public", help="Schema name (default: public)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--table", help="Table name (optionally schema.table)")
    g.add_argument("--list", action="store_true", help="List tables/views in schema")
    args = ap.parse_args()

    engine = connect()
    if args.list:
        list_tables(engine, args.schema)
    else:
        describe_table(engine, args.table, default_schema=args.schema)

if __name__ == "__main__":
    main()
```

**Examples:**

```bash
# From repo root
python -m backend.tools.db_describe --list --schema public
python -m backend.tools.db_describe --table public.users
python -m backend.tools.db_describe --table orders   # assumes public
```

This gives you a quick, scriptable “what columns does this table have?” without opening pgAdmin—and it works nicely with your “run from the git dir” setup.
