Great goal: **store the exact order number string** (unchanged), but **search flexibly** (ignore hyphens/spacing, allow fuzzy).

Below is a simple, fast pattern that plays nicely with PostgreSQL + pgvector-free (no LLM needed) and SQLAlchemy 2.x.

---

# 1) Schema: keep raw, add a normalized/generated column

* Keep `order_number` exactly as-is (text).
* Add a **generated** column that strips all non-digits.
* Add indexes for fast exact/LIKE/fuzzy matches.

```sql
-- Once per database
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS order_number_digits
  TEXT
  GENERATED ALWAYS AS (regexp_replace(order_number, '\D+', '', 'g')) STORED;

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_orders_ordernum_trgm
  ON orders USING gin (order_number gin_trgm_ops);              -- fuzzy on raw text

CREATE INDEX IF NOT EXISTS idx_orders_ordernum_digits_btree
  ON orders (order_number_digits);                               -- exact/prefix

CREATE INDEX IF NOT EXISTS idx_orders_ordernum_digits_trgm
  ON orders USING gin (order_number_digits gin_trgm_ops);        -- fuzzy on digits-only
```

Why both?

* `order_number` GIN(trgm) accelerates fuzzy text searches (`ILIKE`, `similarity`).
* `order_number_digits` B-tree is great for equality/prefix; trigram GIN helps `%...%` matches.

---

# 2) SQLAlchemy model (computed column)

```python
from sqlalchemy import (
    Column, Text, Computed, Index
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id = Column(Text, primary_key=True)
    order_number = Column(Text, nullable=False)

    # Persisted/generated column in Postgres
    order_number_digits = Column(
        Text,
        Computed(r"regexp_replace(order_number, '\D+', '', 'g')", persisted=True),
        nullable=False
    )

    __table_args__ = (
        Index("idx_orders_ordernum_trgm", order_number, postgresql_using="gin",
              postgresql_ops={"order_number": "gin_trgm_ops"}),
        Index("idx_orders_ordernum_digits_btree", order_number_digits),
        Index("idx_orders_ordernum_digits_trgm", order_number_digits, postgresql_using="gin",
              postgresql_ops={"order_number_digits": "gin_trgm_ops"}),
    )
```

> If you use Alembic, add a migration for the column and indexes.

---

# 3) Search function (digits-only queries get de-hyphenized matches)

* If the user’s query is **digits-only**, search the `order_number_digits` column.
* Otherwise, search both the raw and the digits columns.
* Use `ILIKE` for flexible substring search, and `pg_trgm` similarity for typos.

```python
import re
from sqlalchemy import select, or_, and_, func, desc, bindparam
from sqlalchemy.orm import Session

def search_orders(session: Session, query: str, limit: int = 25):
    q = (query or "").strip()
    if not q:
        return []

    # Postgres pg_trgm similarity (works with our GIN trigram indexes)
    similarity = func.similarity

    if q.isdigit():
        # Example: user enters "1234567" and DB has "123-4567" → match on digits column
        qdigits = q
        stmt = (
            select(Order)
            .where(
                or_(
                    Order.order_number_digits == bindparam("qd_eq"),
                    Order.order_number_digits.ilike(bindparam("qd_like")),
                    similarity(Order.order_number_digits, bindparam("qd_sim")) > bindparam("thresh", 0.35),
                )
            )
            .order_by(
                desc(Order.order_number_digits == bindparam("qd_eq2")),
                desc(similarity(Order.order_number_digits, bindparam("qd_sim2"))),
                Order.order_number.asc(),
            )
            .limit(limit)
        ).params(
            qd_eq=qdigits, qd_like=f"%{qdigits}%", qd_sim=qdigits, qd_eq2=qdigits, qd_sim2=qdigits
        )

    else:
        # Mixed input: search raw + digits-stripped
        qdigits = re.sub(r"\D+", "", q)
        conds = [
            Order.order_number.ilike(bindparam("q_like")),
            similarity(Order.order_number, bindparam("q_sim")) > bindparam("th1", 0.35),
        ]
        if qdigits:
            conds.extend([
                Order.order_number_digits.ilike(bindparam("qd_like")),
                similarity(Order.order_number_digits, bindparam("qd_sim")) > bindparam("th2", 0.35),
            ])

        stmt = (
            select(Order)
            .where(or_(*conds))
            .order_by(
                desc(similarity(Order.order_number, bindparam("q_sim2"))),
                desc(similarity(Order.order_number_digits, bindparam("qd_sim2"))) if qdigits else desc(func.nullif(0,0)),
                Order.order_number.asc(),
            )
            .limit(limit)
        ).params(
            q_like=f"%{q}%", q_sim=q, th1=0.35,
            q_sim2=q,
            qd_like=f"%{qdigits}%", qd_sim=qdigits, th2=0.35, qd_sim2=qdigits
        )

    return session.execute(stmt).scalars().all()
```

**Notes**

* `similarity()` requires `CREATE EXTENSION pg_trgm;`.
* You can tweak the `0.35` threshold. Lower = more results (and more noise).
* The trigram GIN indexes will speed up both `ILIKE '%...%'` and `similarity()`.

---

# 4) Examples of what this handles

* DB has `order_number = 'PO-2024-00123'`

  * User searches `202400123` → matches via `order_number_digits = '202400123'`.
* DB has `order_number = 'SO-11-7-88'`

  * User searches `11788` → matches digits column.
* Typos: user types `PO-2024-0012E` → trigram similarity on `order_number` still finds `PO-2024-00123`.

---

# 5) Optional niceties

* If you want **prefix** matches to rank higher, add an `ORDER BY` term like
  `desc(Order.order_number_digits.like(bindparam("qd_prefix")))` with `qd_prefix=f"{qdigits}%"`
* If order numbers are case-insensitive, consider `citext` for `order_number`, or keep it `text` and rely on `ILIKE`.

---

**Bottom line:**

* **Preserve** the raw order number exactly as text.
* Add a **generated “digits-only” column** and **trigram indexes**.
* Route **digits-only queries** to the digits column; otherwise search both.
* Use `ILIKE` + `pg_trgm` `similarity()` for typo-tolerant, hyphen-insensitive search — fast and simple.

---

Good question — these three concepts in SQLAlchemy (`Engine`, `Connection`, `Session`) are related but distinct. They sit at different layers of abstraction between your Python code and the database.

---

## 1. **Engine**

* **What it is**: The *factory* and central object that knows how to talk to a particular database (PostgreSQL, SQLite, MySQL, etc.).
* **What it does**: Manages connection pools, dialects, and DBAPI integration.
* **How you use it**: Create it once at app startup and share it.

```python
from sqlalchemy import create_engine

engine = create_engine("postgresql+psycopg2://user:pass@localhost/dbname")
```

* Think of the `Engine` as the **“database access manager”**. You rarely run queries directly on it — you usually go through `Session` (ORM) or `Connection` (Core).

---

## 2. **Connection**

* **What it is**: A **single DBAPI connection** to the database, borrowed from the `Engine`’s connection pool.
* **What it does**: Executes SQL statements (raw SQLAlchemy Core expressions or text).
* **How you use it**: Best for *short-lived*, explicit interactions where you want full control of transactions.

```python
from sqlalchemy import text

with engine.connect() as conn:   # pulls a connection from pool
    result = conn.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": 42})
    for row in result:
        print(row)
```

* You control `BEGIN` / `COMMIT` (or let the context manager auto-handle).
* `Connection` is lower-level: **no ORM objects, no identity map**, just raw rows and Core constructs.

---

## 3. **Session**

* **What it is**: The **ORM interface**. It manages:

  * An **identity map** (keeps one object per row per session).
  * Unit-of-work pattern (tracks changes, flushes them to the DB).
  * Transactions (usually one per `Session`).
* **What it does**: Wraps a `Connection` behind the scenes, but gives you ORM convenience.
* **How you use it**:

```python
from sqlalchemy.orm import Session

with Session(engine) as session:
    order = session.get(Order, 42)    # ORM fetch
    order.status = "shipped"          # mark dirty
    session.commit()                  # flush + commit transaction
```

* You usually use `Session` for business logic and persistence of Python objects.
* It’s higher-level than `Connection`.

---

## ⚖️ Relationship & Analogy

* **Engine** = the airport terminal. It owns the gates and controls access to planes (connections).
* **Connection** = one airplane. It can take you to/from the database with SQL.
* **Session** = a travel agent. It books flights for you (gets a connection), tracks your itinerary (identity map), and handles the paperwork (transactions).

Or:

* **Engine** → the factory.
* **Connection** → one tool from the factory.
* **Session** → the workshop where tools are used to shape actual objects.

---

## Typical App Lifecycle

1. **Create one Engine** (at startup).
2. **Use Sessions** for business logic (read/write ORM objects).
3. **Drop down to Connection** if you need raw SQL or high-performance bulk ops.

```python
# Startup
engine = create_engine("postgresql+psycopg2://...")

# ORM usage
with Session(engine) as session:
    objs = session.query(Order).filter_by(status="pending").all()

# Core usage
with engine.connect() as conn:
    conn.execute(text("UPDATE orders SET status='archived' WHERE created_at < now() - interval '1 year'"))
    conn.commit()
```

---

👉 So:

* Use **Session** if you’re working with ORM objects.
* Use **Connection** if you want Core / raw SQL control.
* Use **Engine** to configure the database, manage pooling, and create both Sessions and Connections.
