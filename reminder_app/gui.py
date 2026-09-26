"""Интерфейс на tkinter: главное окно, диалог добавления и popup-оповещение."""
from __future__ import annotations

import queue
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

from db import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_LABELS,
    STATUS_OVERDUE,
    STATUS_PENDING,
    Database,
    Reminder,
)
from notifier import ReminderScheduler, toast_backend

FILTER_ALL = "Все"
DATE_FMT = "%d.%m.%Y"
TIME_FMT = "%H:%M"


class App(tk.Tk):
    POLL_MS = 500

    def __init__(self, db: Database, events: queue.Queue, scheduler: ReminderScheduler) -> None:
        super().__init__()
        self.db = db
        self.events = events
        self.scheduler = scheduler
        self._popups: dict[int, AlertPopup] = {}

        self.title("Напоминалка")
        self.geometry("860x560")
        self.minsize(640, 400)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=26)

        self.filter_var = tk.StringVar(value=FILTER_ALL)
        self._build()
        self.refresh()
        self.after(self.POLL_MS, self._poll_events)

    # ---------- построение интерфейса ----------

    def _build(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")

        ttk.Button(bar, text="➕ Добавить", command=self._add).pack(side="left")
        ttk.Button(bar, text="✔ Готово", command=lambda: self._set_status(STATUS_DONE)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="✖ Отменить", command=lambda: self._set_status(STATUS_CANCELLED)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="🗑 Удалить", command=self._delete).pack(side="left", padx=(6, 0))

        combo = ttk.Combobox(
            bar,
            textvariable=self.filter_var,
            values=[FILTER_ALL, *STATUS_LABELS.values()],
            state="readonly",
            width=14,
        )
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ttk.Label(bar, text="Фильтр по статусу:").pack(side="right", padx=(0, 6))

        pane = ttk.PanedWindow(self, orient="vertical")
        pane.pack(fill="both", expand=True, padx=8)

        table = ttk.Frame(pane)
        columns = ("id", "title", "when", "status")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="extended")
        for col, text, width, anchor, stretch in (
            ("id", "№", 50, "center", False),
            ("title", "Заголовок", 380, "w", True),
            ("when", "Дата и время", 150, "center", False),
            ("status", "Статус", 120, "center", False),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, stretch=stretch)
        self.tree.tag_configure(STATUS_OVERDUE, foreground="#c62828")
        self.tree.tag_configure(STATUS_DONE, foreground="#2e7d32")
        self.tree.tag_configure(STATUS_CANCELLED, foreground="#808080")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_details())
        self.tree.bind("<Delete>", lambda _e: self._delete())
        pane.add(table, weight=3)

        details = ttk.LabelFrame(pane, text="Описание", padding=6)
        self.details = tk.Text(details, height=5, wrap="word", state="disabled", relief="flat",
                               font=("Segoe UI", 10))
        self.details.pack(fill="both", expand=True)
        pane.add(details, weight=1)

        self.status_bar = ttk.Label(self, padding=(8, 4), anchor="w")
        self.status_bar.pack(fill="x")

    # ---------- данные ----------

    def refresh(self) -> None:
        selected = set(self.tree.selection())
        label = self.filter_var.get()
        status = next((k for k, v in STATUS_LABELS.items() if v == label), None)

        self.tree.delete(*self.tree.get_children())
        for r in self.db.list(status):
            iid = str(r.id)
            self.tree.insert("", "end", iid=iid, tags=(r.status,), values=(
                r.id, r.title, r.remind_at.strftime(f"{DATE_FMT} {TIME_FMT}"), r.status_label,
            ))
            if iid in selected:
                self.tree.selection_add(iid)

        counts = self.db.counts()
        summary = "   ".join(f"{STATUS_LABELS[s]}: {n}" for s, n in counts.items())
        backend = toast_backend() or "нет (только всплывающие окна)"
        self.status_bar.config(text=f"{summary}      |  Уведомления Windows: {backend}")
        self._show_details()

    def _selected_ids(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def _show_details(self) -> None:
        ids = self._selected_ids()
        text = ""
        if len(ids) == 1 and (r := self.db.get(ids[0])):
            text = r.description or "(без описания)"
        self.details.config(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.config(state="disabled")

    # ---------- действия ----------

    def _add(self) -> None:
        dialog = ReminderDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            title, description, when = dialog.result
            self.db.add(title, description, when)
            self.refresh()
            self.scheduler.wake()

    def _set_status(self, status: str) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Напоминалка", "Выберите напоминание в списке.", parent=self)
            return
        for rid in ids:
            self.db.set_status(rid, status)
            self._close_popup(rid)
        self.refresh()

    def _delete(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Напоминалка", "Выберите напоминание в списке.", parent=self)
            return
        if not messagebox.askyesno("Удаление", f"Удалить выбранные напоминания ({len(ids)} шт.)?", parent=self):
            return
        for rid in ids:
            self.db.delete(rid)
            self._close_popup(rid)
        self.refresh()

    # ---------- события планировщика ----------

    def _poll_events(self) -> None:
        changed = False
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "due":
                    self._show_popup(event[1], event[2])
                changed = True
        except queue.Empty:
            pass
        if changed:
            self.refresh()
        self.after(self.POLL_MS, self._poll_events)

    def _show_popup(self, reminder: Reminder, missed: bool) -> None:
        self._close_popup(reminder.id)
        self._popups[reminder.id] = AlertPopup(self, reminder, missed)

    def _close_popup(self, reminder_id: int) -> None:
        popup = self._popups.pop(reminder_id, None)
        if popup and popup.winfo_exists():
            popup.destroy()

    def popup_closed(self, reminder_id: int) -> None:
        self._popups.pop(reminder_id, None)

    def _on_close(self) -> None:
        answer = messagebox.askyesnocancel(
            "Напоминалка",
            "Свернуть программу вместо выхода?\n\n"
            "Да — свернуть (напоминания продолжат срабатывать)\n"
            "Нет — полностью выйти",
            parent=self,
        )
        if answer is True:
            self.iconify()
        elif answer is False:
            self.destroy()


class ReminderDialog(tk.Toplevel):
    """Модальный диалог создания напоминания. Результат — self.result."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title("Новое напоминание")
        self.transient(master)
        self.resizable(False, False)
        self.result: tuple[str, str, datetime] | None = None

        default = (datetime.now() + timedelta(minutes=15)).replace(second=0, microsecond=0)
        self.title_var = tk.StringVar()
        self.date_var = tk.StringVar(value=default.strftime(DATE_FMT))
        self.time_var = tk.StringVar(value=default.strftime(TIME_FMT))

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Заголовок:").grid(row=0, column=0, sticky="w", pady=4)
        title_entry = ttk.Entry(frm, textvariable=self.title_var, width=46)
        title_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Label(frm, text="Описание:").grid(row=1, column=0, sticky="nw", pady=4)
        self.desc = tk.Text(frm, width=46, height=5, wrap="word", font=("Segoe UI", 10))
        self.desc.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Label(frm, text="Дата (ДД.ММ.ГГГГ):").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.date_var, width=12).grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(frm, text="Время (ЧЧ:ММ):").grid(row=2, column=2, sticky="e", padx=(8, 4))
        ttk.Entry(frm, textvariable=self.time_var, width=7).grid(row=2, column=3, sticky="w", pady=4)

        quick = ttk.Frame(frm)
        quick.grid(row=3, column=1, columnspan=3, sticky="w", pady=(0, 8))
        for text, delta in (("+15 мин", timedelta(minutes=15)), ("+1 час", timedelta(hours=1)),
                            ("+1 день", timedelta(days=1))):
            ttk.Button(quick, text=text, width=8,
                       command=lambda d=delta: self._set_time(datetime.now() + d)).pack(side="left", padx=(0, 4))
        ttk.Button(quick, text="Завтра 9:00", command=self._tomorrow_morning).pack(side="left")

        buttons = ttk.Frame(frm)
        buttons.grid(row=4, column=0, columnspan=4, sticky="e")
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="left")

        self.bind("<Return>", lambda _e: self._save() if self.focus_get() is not self.desc else None)
        self.bind("<Escape>", lambda _e: self.destroy())
        title_entry.focus_set()
        self.grab_set()

    def _set_time(self, dt: datetime) -> None:
        self.date_var.set(dt.strftime(DATE_FMT))
        self.time_var.set(dt.strftime(TIME_FMT))

    def _tomorrow_morning(self) -> None:
        tomorrow = datetime.now() + timedelta(days=1)
        self._set_time(tomorrow.replace(hour=9, minute=0))

    def _save(self) -> None:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Ошибка", "Введите заголовок.", parent=self)
            return
        try:
            when = datetime.strptime(f"{self.date_var.get().strip()} {self.time_var.get().strip()}",
                                     f"{DATE_FMT} {TIME_FMT}")
        except ValueError:
            messagebox.showwarning("Ошибка", "Неверная дата или время.\nФормат: 31.12.2026 и 18:30", parent=self)
            return
        if when <= datetime.now():
            messagebox.showwarning("Ошибка", "Дата и время должны быть в будущем.", parent=self)
            return
        self.result = (title, self.desc.get("1.0", "end").strip(), when)
        self.destroy()


class AlertPopup(tk.Toplevel):
    """Окно-оповещение поверх всех окон. Видно, даже если главное окно свёрнуто."""

    SNOOZE_OPTIONS = {"5 мин": 5, "10 мин": 10, "15 мин": 15, "30 мин": 30, "1 час": 60}

    def __init__(self, app: App, reminder: Reminder, missed: bool) -> None:
        # Не transient: иначе окно свернулось бы вместе с главным.
        super().__init__(app)
        self.app = app
        self.reminder = reminder
        self.title("⏰ Напоминание")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._dismiss)

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        if missed:
            ttk.Label(frm, text="Пропущенное напоминание", foreground="#c62828",
                      font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(frm, text=reminder.title, font=("Segoe UI", 14, "bold"),
                  wraplength=380).pack(anchor="w")
        ttk.Label(frm, text=reminder.remind_at.strftime(f"{DATE_FMT} {TIME_FMT}"),
                  foreground="#555").pack(anchor="w", pady=(2, 8))
        if reminder.description:
            ttk.Label(frm, text=reminder.description, wraplength=380,
                      justify="left").pack(anchor="w", pady=(0, 12))

        row = ttk.Frame(frm)
        row.pack(fill="x")
        ttk.Button(row, text="✔ Готово", command=self._done).pack(side="left")
        self.snooze_var = tk.StringVar(value="10 мин")
        ttk.Button(row, text="Отложить на", command=self._snooze).pack(side="left", padx=(12, 4))
        ttk.Combobox(row, textvariable=self.snooze_var, values=list(self.SNOOZE_OPTIONS),
                     state="readonly", width=7).pack(side="left")
        ttk.Button(row, text="Закрыть", command=self._dismiss).pack(side="right")

        self.update_idletasks()
        w, h = max(self.winfo_reqwidth(), 420), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.after(50, self._grab_attention)

    def _grab_attention(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        self.bell()

    def _done(self) -> None:
        self.app.db.set_status(self.reminder.id, STATUS_DONE)
        self._finish()

    def _snooze(self) -> None:
        minutes = self.SNOOZE_OPTIONS[self.snooze_var.get()]
        self.app.db.snooze(self.reminder.id, datetime.now().replace(microsecond=0) + timedelta(minutes=minutes))
        self._finish()

    def _dismiss(self) -> None:
        # Статус не меняем: если не отметить «Готово», запись станет «Просрочено».
        self._finish()

    def _finish(self) -> None:
        self.app.popup_closed(self.reminder.id)
        self.destroy()
        self.app.refresh()
