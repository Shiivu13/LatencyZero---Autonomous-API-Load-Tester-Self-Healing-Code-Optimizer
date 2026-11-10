import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "demo.db")


def get_db():
    """Returns a SQLite connection with Row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes SQLite schema and seeds test data."""
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL
            )
        """)
        # Intentionally unindexed table for Case 1 demonstration
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Seed data if empty
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            users_data = [(f"User {i}", f"user{i}@example.com") for i in range(1, 51)]
            cur.executemany("INSERT INTO users (name, email) VALUES (?, ?)", users_data)

            orders_data = []
            for user_id in range(1, 51):
                for order_idx in range(1, 11):
                    orders_data.append((
                        user_id,
                        round(19.99 * order_idx, 2),
                        "completed" if order_idx % 2 == 0 else "pending",
                        "2026-11-10T12:00:00"
                    ))
            cur.executemany(
                "INSERT INTO orders (user_id, amount, status, created_at) VALUES (?, ?, ?, ?)",
                orders_data
            )
    conn.close()
