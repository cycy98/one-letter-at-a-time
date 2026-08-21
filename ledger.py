"""SQLite leaderboard storage."""

import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from kernel.game import PlayerId

DB = Path(os.environ.get("DB", "wge.db"))

type LeaderboardRow = tuple[PlayerId, int, int]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results(
    guild  INTEGER NOT NULL,
    player INTEGER NOT NULL,
    won    INTEGER NOT NULL CHECK (won IN (0, 1))
) STRICT;
CREATE INDEX IF NOT EXISTS results_guild ON results(guild, player);
"""


def _connect() -> sqlite3.Connection:
    # Pragmas are per-connection and must precede the first implicit BEGIN, so
    # they run before autocommit is turned off.
    conn = sqlite3.connect(DB, autocommit=True)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.autocommit = False
    return conn


def init() -> None:
    # journal_mode is a database-level, persistent setting and cannot be changed
    # inside a transaction, hence a dedicated autocommit connection.
    conn = sqlite3.connect(DB, autocommit=True)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
    finally:
        conn.close()


def record(guild: int, roster: Iterable[PlayerId], win: PlayerId | None) -> None:
    """Record each human who started the game."""
    rows = [(guild, int(p), int(p == win)) for p in roster if p > 0]
    if not rows:
        return
    conn = _connect()
    try:
        with conn:
            conn.executemany("INSERT INTO results(guild, player, won) VALUES (?,?,?)", rows)
    finally:
        conn.close()


def leaderboard(guild: int, limit: int = 10) -> list[LeaderboardRow]:
    conn = _connect()
    try:
        return [
            (PlayerId(p), w, n)
            for p, w, n in conn.execute(
                """
                SELECT player, SUM(won) AS wins, COUNT(*) AS played
                FROM results WHERE guild = ?
                GROUP BY player ORDER BY wins DESC, played ASC, player ASC LIMIT ?
                """,
                (guild, limit),
            )
        ]
    finally:
        conn.close()
