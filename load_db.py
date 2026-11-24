"""Load orders.csv into a SQLite database (orders.db).

Run:  python load_db.py
Re-running rebuilds the table from the current CSV, so edit the CSV and reload.
Rows are validated with the same rules the MCP server uses (server.read_orders),
so a bad CSV fails here instead of producing a half-loaded database.

Money is stored as integer minor units (e.g. paise) so sums are exact;
divide by 100 for display.
"""

import os
import sqlite3
from decimal import Decimal
from pathlib import Path

from server import DATA_FILE, read_orders

DB_FILE = Path(os.environ.get("ORDERS_DB", str(Path(__file__).with_name("orders.db")))).resolve()

SCHEMA = """
CREATE TABLE orders (
    order_id               TEXT PRIMARY KEY,
    customer_id            TEXT NOT NULL,
    customer_name          TEXT NOT NULL,
    item                   TEXT NOT NULL,
    category               TEXT NOT NULL,
    quantity               INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price_minor       INTEGER NOT NULL CHECK (unit_price_minor >= 0),
    total_minor            INTEGER NOT NULL CHECK (total_minor >= 0),
    currency               TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN
                           ('pending','processing','shipped','delivered','cancelled','returned')),
    order_date             TEXT NOT NULL,   -- YYYY-MM-DD
    expected_delivery_date TEXT,            -- YYYY-MM-DD or NULL
    delivery_date          TEXT,            -- YYYY-MM-DD or NULL
    city                   TEXT NOT NULL
);
CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE INDEX idx_orders_status   ON orders (status);
CREATE INDEX idx_orders_date     ON orders (order_date);
"""


def to_minor(amount: str) -> int:
    return int(Decimal(amount) * 100)


def load() -> int:
    orders = read_orders()  # raises ValueError with the row number if the CSV is invalid
    rows = [
        (
            o["order_id"], o["customer_id"], o["customer_name"], o["item"], o["category"],
            o["quantity"], to_minor(o["unit_price"]), to_minor(o["total_amount"]), o["currency"],
            o["status"], o["order_date"], o["expected_delivery_date"], o["delivery_date"], o["city"],
        )
        for o in orders
    ]
    # Build into a temp file, then swap it in, so a failure never leaves a broken orders.db.
    tmp = DB_FILE.with_name(DB_FILE.name + ".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(f"INSERT INTO orders VALUES ({', '.join('?' * 14)})", rows)
        conn.commit()
    finally:
        conn.close()
    tmp.replace(DB_FILE)
    return len(rows)


if __name__ == "__main__":
    count = load()
    print(f"Loaded {count} orders from {DATA_FILE.name} into {DB_FILE.name}")
