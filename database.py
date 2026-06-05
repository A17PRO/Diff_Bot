"""
database.py — SQLite persistence layer.

Schema (server-scoped):
  ┌─────────────┐        ┌──────────────┐        ┌──────────────────┐
  │   servers   │──(1:N)─│ server_users │─(N:1)──│     users        │
  │─────────────│        │──────────────│        │──────────────────│
  │ server_id PK│        │ server_id FK │        │ discord_id PK    │
  │ server_name │        │ discord_id FK│        │ riot_puuid       │
  │ is_premium  │        └──────────────┘        │ current_lp       │
  └─────────────┘                                │ last_updated_ts  │
                                                 └──────────────────┘
"""

import sqlite3
import time
from contextlib import contextmanager
from typing import Generator, Optional

from config import DATABASE_PATH


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a thread-safe SQLite connection with WAL mode enabled."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation (idempotent)
# ---------------------------------------------------------------------------

# database.py — init_db()
def init_db() -> None:
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                server_id   INTEGER PRIMARY KEY,
                server_name TEXT    NOT NULL,
                is_premium  INTEGER NOT NULL DEFAULT 0
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id             INTEGER PRIMARY KEY,
                riot_puuid             TEXT    NOT NULL UNIQUE,
                current_lp             INTEGER,
                last_updated_timestamp INTEGER
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS server_users (
                server_id  INTEGER NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
                discord_id INTEGER NOT NULL REFERENCES users(discord_id)  ON DELETE CASCADE,
                PRIMARY KEY (server_id, discord_id)
            )""")
    print("[DB] Schema initialised.")


# ---------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------

def upsert_server(server_id: int, server_name: str, is_premium: bool = False) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO servers (server_id, server_name, is_premium)
            VALUES (?, ?, ?)
            ON CONFLICT(server_id) DO UPDATE SET server_name = excluded.server_name
            """,
            (server_id, server_name, int(is_premium)),
        )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def upsert_user(discord_id: int, riot_puuid: str) -> None:
    """Insert or update a user's PUUID binding."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (discord_id, riot_puuid, last_updated_timestamp)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                riot_puuid             = excluded.riot_puuid,
                last_updated_timestamp = excluded.last_updated_timestamp
            """,
            (discord_id, riot_puuid, int(time.time())),
        )


def update_user_lp(discord_id: int, lp: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
               SET current_lp = ?, last_updated_timestamp = ?
             WHERE discord_id = ?
            """,
            (lp, int(time.time()), discord_id),
        )


def get_user_by_discord_id(discord_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
        ).fetchone()


# ---------------------------------------------------------------------------
# Server ↔ User linking
# ---------------------------------------------------------------------------

def link_user_to_server(server_id: int, discord_id: int) -> None:
    """Register <server_id, discord_id> in server_users (idempotent)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO server_users (server_id, discord_id)
            VALUES (?, ?)
            """,
            (server_id, discord_id),
        )


def get_server_users(server_id: int) -> list[sqlite3.Row]:
    """Return all user rows linked to a given server."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.*
              FROM users u
              JOIN server_users su ON su.discord_id = u.discord_id
             WHERE su.server_id = ?
            """,
            (server_id,),
        ).fetchall()
