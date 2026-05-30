"""All SQLite reads and writes — deduplication, listing persistence, and approval state tracking."""

import sqlite3
import os
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "nestiq.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT,
    price           REAL,
    size            REAL,
    rooms           REAL,
    district        TEXT,
    available_from  TEXT,
    first_photo_url TEXT,
    description     TEXT,
    score           REAL,
    decision        TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL
)
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_CREATE_TABLE)


init_db()


def is_seen(url: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM listings WHERE url = ?", (url,)).fetchone()
        return row is not None


def save_listing(listing: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO listings
                (url, title, price, size, rooms, district, available_from,
                 first_photo_url, description, score, decision, created_at)
            VALUES
                (:url, :title, :price, :size, :rooms, :district, :available_from,
                 :first_photo_url, :description, :score, 'pending', :created_at)
            """,
            {**listing, "created_at": now},
        )


def mark_decision(url: str, decision: str) -> None:
    if decision not in ("yes", "no", "pending"):
        raise ValueError(f"Invalid decision: {decision!r}")
    with _connect() as conn:
        conn.execute("UPDATE listings SET decision = ? WHERE url = ?", (decision, url))


def get_pending_listings() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE decision = 'pending' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
