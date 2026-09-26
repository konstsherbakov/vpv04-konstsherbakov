"""Точка входа. Запуск:  python main.py  [--minimized]"""
from __future__ import annotations

import ctypes
import logging
import queue
import sys

from db import Database
from gui import App
from notifier import ReminderScheduler


def enable_dpi_awareness() -> None:
    """Чёткий интерфейс на экранах с масштабированием (Windows 10/11)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    enable_dpi_awareness()

    db = Database()  # создаёт таблицы, если их нет
    events: queue.Queue = queue.Queue()
    scheduler = ReminderScheduler(db, events)

    app = App(db, events, scheduler)
    if "--minimized" in sys.argv:
        app.iconify()

    scheduler.start()
    try:
        app.mainloop()
    finally:
        scheduler.stop()


if __name__ == "__main__":
    main()
