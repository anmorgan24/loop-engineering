"""Schema, seed data, and connection helpers.

The schema is small on purpose: six readable tables plus one the agent is not
allowed to touch. Several traps are deliberate, because the point of the talk is
that a query which executes cleanly is not a query that is correct.

Traps, and the rung of the verifier ladder that catches each:

  1. products.list_price vs order_items.unit_price
     Revenue computed from list_price ignores discounts. Executes fine, wrong
     number. Caught at rung 5 by reconciliation, never by rungs 1-4.

  2. orders.status in (completed, pending, cancelled, refunded)
     Revenue that includes cancelled and refunded orders executes fine.
     Caught at rung 5 by a magnitude band, or offline at rung 6.

  3. customers.region_id is nullable (~12 percent)
     An inner join to regions silently drops those customers.
     Caught at rung 5 by a row-count reconciliation against the unjoined count.

  4. products.is_active soft delete
     Inactive products still have historical order rows. "How many products do
     we sell" has two defensible answers and one intended one.

  5. Join fan-out
     orders joined to order_items multiplies order rows. COUNT(*) over the join
     is not the order count. Caught at rung 5 by a magnitude band.

  6. order_date vs shipped_date, and shipped_date is null for unshipped orders

  7. The payments table appears in the catalog and every read is refused.
     This is the hard blocker. Two questions in the suite require it.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import random
from dataclasses import dataclass

import duckdb

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "shop.duckdb"

# The agent can see this table in the catalog and cannot read it. Any attempt
# raises PermissionDenied, which is a hard blocker: no number of retries helps.
DENIED_TABLES = {"payments"}

SCHEMA_SQL = """
CREATE TABLE regions (
    region_id    INTEGER PRIMARY KEY,
    region_name  VARCHAR NOT NULL,
    country      VARCHAR NOT NULL
);

CREATE TABLE categories (
    category_id    INTEGER PRIMARY KEY,
    category_name  VARCHAR NOT NULL
);

CREATE TABLE customers (
    customer_id    INTEGER PRIMARY KEY,
    customer_name  VARCHAR NOT NULL,
    email          VARCHAR NOT NULL,
    region_id      INTEGER,          -- nullable on purpose
    signup_date    DATE NOT NULL,
    is_churned     BOOLEAN NOT NULL
);

CREATE TABLE products (
    product_id    INTEGER PRIMARY KEY,
    product_name  VARCHAR NOT NULL,
    category_id   INTEGER NOT NULL,
    list_price    DECIMAL(10,2) NOT NULL,   -- catalogue price, not paid price
    is_active     BOOLEAN NOT NULL
);

CREATE TABLE orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL,
    order_date    DATE NOT NULL,
    status        VARCHAR NOT NULL,  -- completed | pending | cancelled | refunded
    shipped_date  DATE               -- null until shipped
);

CREATE TABLE order_items (
    order_item_id  INTEGER PRIMARY KEY,
    order_id       INTEGER NOT NULL,
    product_id     INTEGER NOT NULL,
    quantity       INTEGER NOT NULL,
    unit_price     DECIMAL(10,2) NOT NULL,  -- price actually charged
    discount       DECIMAL(4,3) NOT NULL    -- 0.000 to 0.400
);

CREATE TABLE payments (
    payment_id   INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL,
    method       VARCHAR NOT NULL,
    amount       DECIMAL(10,2) NOT NULL,
    captured_at  TIMESTAMP NOT NULL
);
"""

REGIONS = [
    (1, "North East", "US"),
    (2, "West Coast", "US"),
    (3, "Midwest", "US"),
    (4, "DACH", "DE"),
    (5, "Nordics", "SE"),
    (6, "UK and Ireland", "GB"),
]

CATEGORIES = [
    (1, "Keyboards"),
    (2, "Monitors"),
    (3, "Audio"),
    (4, "Cables"),
    (5, "Laptop Stands"),
    (6, "Webcams"),
    (7, "Storage"),
    (8, "Docking Stations"),
]

STATUSES = ["completed"] * 68 + ["pending"] * 14 + ["cancelled"] * 11 + ["refunded"] * 7


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    dtype: str
    nullable: bool


def seed(path: pathlib.Path = DB_PATH, rng_seed: int = 20260923) -> pathlib.Path:
    """Build the database from scratch. Deterministic given rng_seed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    rng = random.Random(rng_seed)
    conn = duckdb.connect(str(path))
    conn.execute(SCHEMA_SQL)

    conn.executemany("INSERT INTO regions VALUES (?, ?, ?)", REGIONS)
    conn.executemany("INSERT INTO categories VALUES (?, ?)", CATEGORIES)

    first = ["Ada", "Grace", "Alan", "Edsger", "Barbara", "Ken", "Dennis", "Radia",
             "Margaret", "Donald", "Frances", "Jean", "Leslie", "Anita", "Shafi"]
    last = ["Okafor", "Lindgren", "Mueller", "Novak", "Tanaka", "Byrne", "Costa",
            "Haddad", "Silva", "Kowalski", "Nguyen", "Ferrara", "Ivanov", "Park"]

    customers = []
    for cid in range(1, 201):
        name = f"{rng.choice(first)} {rng.choice(last)}"
        email = f"user{cid}@example.com"
        # 12 percent have no region. An inner join to regions drops them.
        region = None if rng.random() < 0.12 else rng.choice(REGIONS)[0]
        signup = dt.date(2024, 1, 1) + dt.timedelta(days=rng.randint(0, 640))
        churned = rng.random() < 0.18
        customers.append((cid, name, email, region, signup, churned))
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers)

    adjectives = ["Compact", "Pro", "Ultra", "Studio", "Nomad", "Core", "Slim", "Max"]
    nouns = {
        1: ["Mechanical Keyboard", "Split Keyboard", "Low Profile Keyboard"],
        2: ["27in Monitor", "32in Monitor", "Ultrawide Monitor"],
        3: ["Headset", "Desk Microphone", "Speaker Pair"],
        4: ["USB-C Cable", "HDMI Cable", "Thunderbolt Cable"],
        5: ["Laptop Riser", "Adjustable Stand", "Folding Stand"],
        6: ["1080p Webcam", "4K Webcam", "Conference Cam"],
        7: ["1TB SSD", "2TB SSD", "Portable Drive"],
        8: ["USB-C Dock", "Travel Dock", "Thunderbolt Dock"],
    }

    products = []
    pid = 1
    for cat_id, _ in CATEGORIES:
        for base in nouns[cat_id]:
            for _ in range(2):
                name = f"{rng.choice(adjectives)} {base}"
                price = round(rng.uniform(19, 749), 2)
                # 15 percent retired, but their historical order rows remain.
                active = rng.random() > 0.15
                products.append((pid, name, cat_id, price, active))
                pid += 1
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", products)

    price_by_pid = {p[0]: float(p[3]) for p in products}

    orders, items = [], []
    item_id = 1
    for oid in range(1, 1301):
        cust = rng.randint(1, 200)
        odate = dt.date(2024, 3, 1) + dt.timedelta(days=rng.randint(0, 700))
        status = rng.choice(STATUSES)
        if status in ("completed", "refunded"):
            shipped = odate + dt.timedelta(days=rng.randint(1, 9))
        else:
            shipped = None  # pending and cancelled orders never shipped
        orders.append((oid, cust, odate, status, shipped))

        for _ in range(rng.randint(1, 4)):
            prod = rng.randint(1, len(products))
            qty = rng.randint(1, 5)
            discount = round(rng.choice([0.0, 0.0, 0.0, 0.05, 0.10, 0.15, 0.25, 0.40]), 3)
            # Paid price drifts from list price even before the discount.
            unit = round(price_by_pid[prod] * rng.uniform(0.94, 1.0), 2)
            items.append((item_id, oid, prod, qty, unit, discount))
            item_id += 1

    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", items)

    methods = ["card", "invoice", "wire", "paypal"]
    payments = []
    pay_id = 1
    for oid, _, odate, status, _ in orders:
        if status in ("completed", "refunded"):
            amount = round(rng.uniform(40, 2400), 2)
            captured = dt.datetime.combine(odate, dt.time(rng.randint(8, 20)))
            payments.append((pay_id, oid, rng.choice(methods), amount, captured))
            pay_id += 1
    conn.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?)", payments)

    # Fold the WAL into the database file so the seed leaves one artefact
    # rather than two, and so a read-only open later does not need to recover.
    conn.execute("CHECKPOINT")
    conn.close()
    return path


def connect(path: pathlib.Path = DB_PATH, read_only: bool = True):
    if not path.exists():
        seed(path)
    return duckdb.connect(str(path), read_only=read_only)


def schema_map(conn) -> dict[str, list[ColumnInfo]]:
    """Table name -> column list, excluding tables the agent may not read.

    Rung 2 of the verifier resolves identifiers against this, which catches a
    hallucinated column before the query ever reaches the engine.
    """
    rows = conn.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'main'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()

    out: dict[str, list[ColumnInfo]] = {}
    for table, col, dtype, nullable in rows:
        out.setdefault(table, []).append(
            ColumnInfo(name=col, dtype=dtype, nullable=(nullable == "YES"))
        )
    return out


def render_schema(conn, include_denied: bool = True) -> str:
    """Human readable catalogue, used as the response to list_tables."""
    lines = []
    for table, cols in sorted(schema_map(conn).items()):
        if table in DENIED_TABLES:
            if not include_denied:
                continue
            lines.append(f"{table}  [RESTRICTED - reads are refused]")
        else:
            lines.append(table)
        for c in cols:
            null = "" if not c.nullable else " NULL"
            lines.append(f"    {c.name} {c.dtype}{null}")
    return "\n".join(lines)


if __name__ == "__main__":
    p = seed()
    conn = duckdb.connect(str(p), read_only=True)
    print(f"seeded {p}")
    for t in ["regions", "categories", "customers", "products", "orders",
              "order_items", "payments"]:
        n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t:<14} {n:>6}")
