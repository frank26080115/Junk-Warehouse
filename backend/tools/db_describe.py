# backend/tools/db_describe.py
# run it from the repo root or backend/. It uses your backend/.env (DATABASE_URL).

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
