"""
seed_db.py
----------
Creates tickets.db (SQLite) -- the local data source the agent's
`lookup_order` tool reads from. Standing in for a real orders/CRM table
at a small freelance web3/dev agency (Web3Geeks).

Run directly to (re)build the database:
    python seed_db.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "tickets.db"

ORDERS = [
    # order_id, customer_email, product, amount_usd, currency_pref, status, purchased_on
    ("ORD-1001", "amelia@example.com", "Smart Contract Audit - Standard", 450.00, "USD", "completed", "2026-07-02"),
    ("ORD-1002", "luca.rossi@example.it", "NFT Minting Site - Starter", 220.00, "EUR", "completed", "2026-07-10"),
    ("ORD-1003", "kenji@example.jp", "DeFi Dashboard - Pro", 890.00, "JPY", "completed", "2026-07-18"),
    ("ORD-1004", "sara@example.com", "Wallet Integration - Basic", 150.00, "USD", "refunded", "2026-06-28"),
    ("ORD-1005", "omar@example.pk", "Smart Contract Audit - Standard", 450.00, "USD", "completed", "2026-08-01"),
]


def build_database() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS orders")
    cur.execute(
        """
        CREATE TABLE orders (
            order_id      TEXT PRIMARY KEY,
            customer_email TEXT NOT NULL,
            product       TEXT NOT NULL,
            amount_usd    REAL NOT NULL,
            currency_pref TEXT NOT NULL,
            status        TEXT NOT NULL,
            purchased_on  TEXT NOT NULL
        )
        """
    )
    cur.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", ORDERS
    )
    conn.commit()
    conn.close()
    print(f"Seeded {len(ORDERS)} orders into {DB_PATH}")


if __name__ == "__main__":
    build_database()
