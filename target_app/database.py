import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "demo.db")

NUM_USERS = 5000
ORDERS_PER_USER = 10


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the schema and seed it. Re-seeds if the row counts don't match."""
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL
            )
        """)
        # No index on orders.user_id on purpose: every lookup by user is a full scan.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        expected_orders = NUM_USERS * ORDERS_PER_USER
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if users != NUM_USERS or orders != expected_orders:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM users")
            conn.executemany(
                "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
                [(i, f"User {i}", f"user{i}@example.com") for i in range(1, NUM_USERS + 1)],
            )
            conn.executemany(
                "INSERT INTO orders (user_id, amount, status, created_at) VALUES (?, ?, ?, ?)",
                [
                    (
                        user_id,
                        round(19.99 * n, 2),
                        "completed" if n % 2 == 0 else "pending",
                        "2026-11-10T12:00:00",
                    )
                    for user_id in range(1, NUM_USERS + 1)
                    for n in range(1, ORDERS_PER_USER + 1)
                ],
            )
    conn.close()
