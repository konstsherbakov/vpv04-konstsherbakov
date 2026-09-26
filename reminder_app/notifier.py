"""Логика уведомлений: фоновый планировщик и системные toast-уведомления Windows.

Планировщик работает в отдельном потоке и не зависит от состояния окна
(свёрнуто, скрыто) — он сам ходит в БД, показывает toast и кладёт событие
в очередь, которую GUI-поток забирает и показывает popup поверх всех окон.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timedelta

from db import Database, Reminder

log = logging.getLogger(__name__)

APP_NAME = "Напоминалка"

# Предпочтительно winotify (нативные toast Windows 10/11).
# win10toast — запасной вариант: на новых версиях Python он работает нестабильно.
try:
    from winotify import Notification, audio

    _BACKEND = "winotify"
except ImportError:  # pragma: no cover - зависит от окружения
    try:
        from win10toast import ToastNotifier

        _toaster = ToastNotifier()
        _BACKEND = "win10toast"
    except ImportError:
        _BACKEND = None


def toast_backend() -> str | None:
    return _BACKEND


def show_toast(title: str, message: str) -> None:
    """Показывает системное уведомление Windows. Ошибки не пробрасывает."""
    message = message or " "
    try:
        if _BACKEND == "winotify":
            note = Notification(app_id=APP_NAME, title=title, msg=message[:250], duration="long")
            note.set_audio(audio.Reminder, loop=False)
            note.show()
        elif _BACKEND == "win10toast":
            _toaster.show_toast(title, message[:250], duration=10, threaded=True)
    except Exception:
        log.exception("Не удалось показать системное уведомление")


class ReminderScheduler(threading.Thread):
    """Раз в `interval` секунд проверяет БД.

    События, которые кладутся в очередь `events`:
        ("due", Reminder, missed: bool) — наступило время напоминания;
        ("changed",)                   — статусы изменились (нужно обновить список).
    """

    def __init__(
        self,
        db: Database,
        events: queue.Queue,
        interval: float = 5.0,
        overdue_after: timedelta = timedelta(minutes=15),
    ) -> None:
        super().__init__(name="ReminderScheduler", daemon=True)
        self.db = db
        self.events = events
        self.interval = interval
        self.overdue_after = overdue_after
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def wake(self) -> None:
        """Запустить проверку немедленно (например, после добавления напоминания)."""
        self._wake_event.set()

    def run(self) -> None:
        log.info("Планировщик запущен (toast: %s)", _BACKEND or "нет")
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Ошибка в цикле планировщика")
            self._wake_event.wait(self.interval)
            self._wake_event.clear()

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()

        for reminder in self.db.due_unnotified(now):
            self.db.mark_notified(reminder.id)
            missed = now - reminder.remind_at > self.overdue_after
            self._notify(reminder, missed)
            self.events.put(("due", reminder, missed))

        # Показанные, но так и не закрытые за overdue_after — «Просрочено».
        if self.db.mark_overdue(now - self.overdue_after):
            self.events.put(("changed",))

    @staticmethod
    def _notify(reminder: Reminder, missed: bool) -> None:
        when = reminder.remind_at.strftime("%d.%m.%Y %H:%M")
        prefix = "Пропущено: " if missed else "⏰ "
        body = f"{when}\n{reminder.description}" if reminder.description else when
        show_toast(prefix + reminder.title, body)
