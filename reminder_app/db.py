"""Слой хранения: SQLite3.

Каждая операция открывает своё короткое соединение, поэтому класс Database
безопасно использовать одновременно из GUI-потока и из потока планировщика.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().with_name("reminders.db")

# Даты храним строкой фиксированного формата: такие строки корректно
# сравниваются лексикографически прямо в SQL.
DT_FORMAT = "%Y-%m-%d %H:%M:%S"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_OVERDUE = "overdue"
STATUS_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATUS_PENDING: "Ожидает",
    STATUS_DONE: "Готово",
    STATUS_OVERDUE: "Просрочено",
    STATUS_CANCELLED: "Отменено",
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT    NOT NULL DEFAULT '',
    remind_at   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT '{STATUS_PENDING}'
                CHECK (status IN ('{STATUS_PENDING}', '{STATUS_DONE}',
                                  '{STATUS_OVERDUE}', '{STATUS_CANCELLED}')),
    notified    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_status_time
    ON reminders (status, remind_at);
"""


def to_db(dt: datetime) -> str:
    return dt.strftime(DT_FORMAT)


def from_db(value: str) -> datetime:
    return datetime.strptime(value, DT_FORMAT)


@dataclass(frozen=True)
class Reminder:
    id: int
    title: str
    description: str
    remind_at: datetime
    status: str
    notified: bool
    created_at: datetime

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Reminder":
        return cls(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            remind_at=from_db(row["remind_at"]),
            status=row["status"],
            notified=bool(row["notified"]),
            created_at=from_db(row["created_at"]),
        )


class Database:
    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = str(path)
        self.init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commit при успехе, rollback при исключении
                yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Проверяет наличие таблиц и создаёт их при необходимости."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    # ---------- CRUD ----------

    def add(self, title: str, description: str, remind_at: datetime) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (title, description, remind_at, created_at) "
                "VALUES (?, ?, ?, ?)",
                (title.strip(), description.strip(), to_db(remind_at), to_db(datetime.now())),
            )
            return cur.lastrowid

    def get(self, reminder_id: int) -> Reminder | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return Reminder.from_row(row) if row else None

    def list(self, status: str | None = None) -> list[Reminder]:
        """Все напоминания (или только с указанным статусом), по времени срабатывания."""
        sql = "SELECT * FROM reminders"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY remind_at, id"
        with self._connect() as conn:
            return [Reminder.from_row(r) for r in conn.execute(sql, params)]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM reminders GROUP BY status")
            result = {s: 0 for s in STATUS_LABELS}
            result.update({r["status"]: r["n"] for r in rows})
            return result

    def delete(self, reminder_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    def set_status(self, reminder_id: int, status: str) -> None:
        if status not in STATUS_LABELS:
            raise ValueError(f"Неизвестный статус: {status}")
        with self._connect() as conn:
            conn.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))

    def snooze(self, reminder_id: int, until: datetime) -> None:
        """Переносит напоминание: снова «Ожидает» и сработает ещё раз."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE reminders SET remind_at = ?, status = ?, notified = 0 WHERE id = ?",
                (to_db(until), STATUS_PENDING, reminder_id),
            )

    # ---------- для планировщика ----------

    def due_unnotified(self, now: datetime) -> list[Reminder]:
        """Ожидающие напоминания, время которых наступило, а уведомления ещё не было."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders "
                "WHERE status = ? AND notified = 0 AND remind_at <= ? "
                "ORDER BY remind_at",
                (STATUS_PENDING, to_db(now)),
            )
            return [Reminder.from_row(r) for r in rows]

    def mark_notified(self, reminder_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,))

    def mark_overdue(self, deadline: datetime) -> int:
        """Переводит в «Просрочено» показанные, но не закрытые до deadline напоминания.

        Возвращает количество изменённых записей.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE reminders SET status = ? "
                "WHERE status = ? AND notified = 1 AND remind_at <= ?",
                (STATUS_OVERDUE, STATUS_PENDING, to_db(deadline)),
            )
            return cur.rowcount
