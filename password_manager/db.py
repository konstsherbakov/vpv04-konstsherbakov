"""Слой хранения: SQLite3. Таблицы создаются при старте, если их нет."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().with_name("passwords.db")
DT_FORMAT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS master (
    id         INTEGER PRIMARY KEY CHECK (id = 1),  -- всегда одна строка
    salt       BLOB    NOT NULL,
    hash       TEXT    NOT NULL,                    -- SHA-256, hex
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE COLLATE NOCASE
               CHECK (length(trim(name)) > 0),
    login      TEXT    NOT NULL,
    password   BLOB    NOT NULL,                    -- токен Fernet
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
"""


def _now() -> str:
    return datetime.now().strftime(DT_FORMAT)


@dataclass(frozen=True)
class Entry:
    id: int
    name: str
    login: str
    password: bytes  # зашифрованный
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Entry":
        return cls(**{key: row[key] for key in row.keys()})


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commit при успехе, rollback при исключении
                yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ---------- мастер-пароль ----------

    def get_master(self) -> tuple[bytes, str] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT salt, hash FROM master WHERE id = 1").fetchone()
        return (row["salt"], row["hash"]) if row else None

    def set_master(self, salt: bytes, digest: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO master (id, salt, hash, created_at) VALUES (1, ?, ?, ?)",
                (salt, digest, _now()),
            )

    # ---------- записи ----------

    def add_entry(self, name: str, login: str, encrypted_password: bytes) -> None:
        """Бросает sqlite3.IntegrityError, если запись с таким названием уже есть."""
        now = _now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO entries (name, login, password, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name.strip(), login, encrypted_password, now, now),
            )

    def get_entry(self, name: str) -> Entry | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM entries WHERE name = ?", (name.strip(),)).fetchone()
        return Entry.from_row(row) if row else None

    def list_entries(self) -> list[Entry]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM entries ORDER BY name").fetchall()
        return [Entry.from_row(row) for row in rows]

    def count_entries(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    def update_password(self, name: str, encrypted_password: bytes) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE entries SET password = ?, updated_at = ? WHERE name = ?",
                (encrypted_password, _now(), name.strip()),
            )
        return cur.rowcount > 0

    def delete_entry(self, name: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM entries WHERE name = ?", (name.strip(),))
        return cur.rowcount > 0
