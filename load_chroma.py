"""Index the orders in orders.db into a Chroma vector database (chroma_db/).

Run after load_db.py:   python load_db.py && python load_chroma.py
Re-running syncs the collection in place: new orders are added, changed ones
updated, and orders no longer in orders.db deleted, so it matches exactly.

Each order becomes one record:
  id        -> order_id (use it to fetch the full, exact row from SQLite)
  document  -> "Wireless mouse (Electronics)"   (the text searched by meaning)
  metadata  -> status, city, customer_id, order_date (for exact `where` filters)

The first run downloads Chroma's default embedding model (~80 MB) to ~/.cache/chroma.
"""

import os
import sqlite3
from pathlib import Path

import chromadb

from load_db import DB_FILE

CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", str(Path(__file__).with_name("chroma_db")))).resolve()
COLLECTION = "order_items"


def load() -> int:
    if not DB_FILE.exists():
        raise SystemExit(f"{DB_FILE.name} not found. Run `python load_db.py` first.")
    conn = sqlite3.connect(DB_FILE)
    try:
        rows = conn.execute(
            "SELECT order_id, item, category, status, city, customer_id, order_date FROM orders"
        ).fetchall()
    finally:
        conn.close()

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    col = client.get_or_create_collection(COLLECTION)
    # Sync in place (never delete the collection), so searches never see it empty.
    if rows:
        col.upsert(  # add new orders, update changed ones
            ids=[r[0] for r in rows],
            documents=[f"{r[1]} ({r[2]})" for r in rows],
            metadatas=[
                {"status": r[3], "city": r[4], "customer_id": r[5], "order_date": r[6]}
                for r in rows
            ],
        )
    stale = set(col.get(include=[])["ids"]) - {r[0] for r in rows}
    if stale:
        col.delete(ids=sorted(stale))  # orders no longer in orders.db
    return col.count()


if __name__ == "__main__":
    count = load()
    print(f"Indexed {count} orders from {DB_FILE.name} into {CHROMA_DIR.name}/ (collection '{COLLECTION}')")
