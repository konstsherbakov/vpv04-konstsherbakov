"""Менеджер паролей (CLI). Запуск:

    python main.py                 меню (вход один раз, дальше выбор пунктов 1-6)
    python main.py add [название]
    python main.py get <название>
    python main.py list
    python main.py delete <название> [-y]
    python main.py new [название] [-l 20] [--no-symbols]
"""
from __future__ import annotations

import argparse
import shlex
import sqlite3
import sys
from getpass import getpass

import crypto
from db import Database

MAX_LOGIN_ATTEMPTS = 3
MIN_MASTER_LENGTH = 6


class AppError(Exception):
    """Ошибка, о которой нужно просто сообщить пользователю."""


# ---------- ввод ----------

def ask(prompt: str, allow_empty: bool = False) -> str:
    while True:
        value = input(prompt).strip()
        if value or allow_empty:
            return value
        print("  Значение не может быть пустым.")


def ask_secret(prompt: str) -> str:
    return getpass(prompt)


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes", "д", "да"}


# ---------- приложение ----------

class PasswordManager:
    def __init__(self) -> None:
        self.db = Database()  # создаёт таблицы, если их нет
        self.fernet = crypto.make_fernet(self._load_key())
        self.just_logged_in = False

    def _load_key(self) -> bytes:
        # Без старого ключа сохранённые пароли не расшифровать — не подменяем его молча новым.
        if not crypto.key_exists() and self.db.count_entries() > 0:
            raise AppError(
                f"Файл ключа {crypto.KEY_PATH.name} не найден, а в базе есть записи.\n"
                "Верните файл .key на место — без него пароли не расшифровать."
            )
        return crypto.load_or_create_key()

    # --- вход ---

    def login(self) -> None:
        stored = self.db.get_master()
        if stored is None:
            self._create_master()
        else:
            self._check_master(*stored)
        self.just_logged_in = True

    def _create_master(self) -> None:
        print("Первый запуск. Придумайте мастер-пароль для входа.")
        while True:
            password = ask_secret("Новый мастер-пароль: ")
            if len(password) < MIN_MASTER_LENGTH:
                print(f"  Минимум {MIN_MASTER_LENGTH} символов.")
                continue
            if ask_secret("Повторите: ") != password:
                print("  Пароли не совпадают.")
                continue
            break
        self.db.set_master(*crypto.hash_master(password))
        print("Мастер-пароль сохранён.\n")

    def _check_master(self, salt: bytes, digest: str) -> None:
        for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
            if crypto.verify_master(ask_secret("Мастер-пароль: "), salt, digest):
                return
            left = MAX_LOGIN_ATTEMPTS - attempt
            if left:
                print(f"  Неверный пароль. Осталось попыток: {left}")
        raise AppError("Доступ запрещён.")

    def reconfirm_master(self) -> None:
        """Повторный запрос мастер-пароля перед показом пароля (кроме случая, когда вход был только что)."""
        if self.just_logged_in:
            return
        salt, digest = self.db.get_master()
        if not crypto.verify_master(ask_secret("Подтвердите мастер-пароль: "), salt, digest):
            raise AppError("Неверный мастер-пароль.")

    # --- команды ---

    def cmd_add(self, args: argparse.Namespace) -> None:
        name = args.name or ask("Название (откуда): ")
        if self.db.get_entry(name):
            raise AppError(f"Запись «{name}» уже есть. Удалите её или смените пароль командой new.")
        login = ask("Логин: ")
        password = ask_secret("Пароль (Enter — сгенерировать): ")
        if not password:
            password = crypto.generate_password()
            print(f"Сгенерирован пароль: {password}")
        try:
            self.db.add_entry(name, login, crypto.encrypt(self.fernet, password))
        except sqlite3.IntegrityError:
            raise AppError(f"Запись «{name}» уже есть.") from None
        print(f"Запись «{name}» добавлена.")

    def cmd_get(self, args: argparse.Namespace) -> None:
        name = args.name or ask("Название: ")
        entry = self.db.get_entry(name)
        if entry is None:
            raise AppError(f"Запись «{name}» не найдена.")
        self.reconfirm_master()
        try:
            password = crypto.decrypt(self.fernet, entry.password)
        except crypto.InvalidToken:
            raise AppError("Не удалось расшифровать пароль: ключ .key не подходит к этой записи.") from None
        print(f"Название: {entry.name}\nЛогин:    {entry.login}\nПароль:   {password}")

    def cmd_list(self, args: argparse.Namespace) -> None:
        entries = self.db.list_entries()
        if not entries:
            print("Записей пока нет. Добавьте первую: пункт 1 меню или команда add.")
            return
        name_w = max(len("Название"), *(len(e.name) for e in entries))
        login_w = max(len("Логин"), *(len(e.login) for e in entries))
        print(f"{'Название':<{name_w}}  {'Логин':<{login_w}}  Изменено")
        print(f"{'-' * name_w}  {'-' * login_w}  {'-' * 19}")
        for e in entries:
            print(f"{e.name:<{name_w}}  {e.login:<{login_w}}  {e.updated_at}")
        print(f"\nВсего: {len(entries)}")

    def cmd_delete(self, args: argparse.Namespace) -> None:
        name = args.name or ask("Название: ")
        if self.db.get_entry(name) is None:
            raise AppError(f"Запись «{name}» не найдена.")
        if not args.yes and not confirm(f"Удалить запись «{name}»?"):
            print("Отменено.")
            return
        self.db.delete_entry(name)
        print(f"Запись «{name}» удалена.")

    def cmd_new(self, args: argparse.Namespace) -> None:
        try:
            password = crypto.generate_password(args.length, use_symbols=not args.no_symbols)
        except ValueError as exc:
            raise AppError(str(exc)) from None
        print(f"Новый пароль: {password}")

        name = args.name or ask("Сохранить под названием (Enter — не сохранять): ", allow_empty=True)
        if not name:
            return
        encrypted = crypto.encrypt(self.fernet, password)
        if self.db.get_entry(name):
            if not confirm(f"Запись «{name}» есть. Заменить её пароль новым?"):
                print("Отменено.")
                return
            self.db.update_password(name, encrypted)
            print(f"Пароль записи «{name}» обновлён.")
        else:
            login = ask("Логин: ")
            self.db.add_entry(name, login, encrypted)
            print(f"Запись «{name}» добавлена.")


# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Менеджер паролей: SQLite3 + шифрование Fernet.",
        epilog="Без команды запускается интерактивный режим.",
    )
    sub = parser.add_subparsers(dest="command", metavar="КОМАНДА")

    p = sub.add_parser("add", help="добавить новую запись")
    p.add_argument("name", nargs="?", help="название (откуда), например Google")

    p = sub.add_parser("get", help="показать логин и пароль по названию")
    p.add_argument("name", nargs="?")

    sub.add_parser("list", help="список записей (названия и логины)")

    p = sub.add_parser("delete", help="удалить запись по названию")
    p.add_argument("name", nargs="?")
    p.add_argument("-y", "--yes", action="store_true", help="не спрашивать подтверждение")

    p = sub.add_parser("new", help="сгенерировать новый пароль (и сохранить/обновить запись)")
    p.add_argument("name", nargs="?", help="сохранить в эту запись (новую или существующую)")
    p.add_argument("-l", "--length", type=int, default=16, help="длина пароля (по умолчанию 16)")
    p.add_argument("--no-symbols", action="store_true", help="только буквы и цифры")
    return parser


COMMANDS = {
    "add": PasswordManager.cmd_add,
    "get": PasswordManager.cmd_get,
    "list": PasswordManager.cmd_list,
    "delete": PasswordManager.cmd_delete,
    "new": PasswordManager.cmd_new,
}


def run_command(app: PasswordManager, args: argparse.Namespace) -> None:
    try:
        COMMANDS[args.command](app, args)
    except AppError as exc:
        print(f"Ошибка: {exc}")


MENU = {
    "1": ("add", "Добавить новый пароль"),
    "2": ("get", "Получить пароль"),
    "3": ("list", "Список всех паролей"),
    "4": ("delete", "Удалить пароль"),
    "5": ("new", "Сгенерировать пароль"),
    "6": ("exit", "Выход"),
}
EXIT_WORDS = {"6", "exit", "quit", "q"}
LINE = "=" * 50


def print_menu() -> None:
    print(f"\n{LINE}\n{'МЕНЕДЖЕР ПАРОЛЕЙ':^50}\n{LINE}")
    for number, (_, title) in MENU.items():
        print(f"{number}. {title}")
    print(LINE)


def args_from_menu(command: str) -> argparse.Namespace:
    """Аргументы для пункта меню: название и прочее спросит сам обработчик команды."""
    args = argparse.Namespace(command=command, name=None, yes=False, length=16, no_symbols=False)
    if command == "new":
        length = ask("Длина пароля (Enter — 16): ", allow_empty=True)
        if length:
            if not length.isdigit():
                raise AppError("Длина должна быть числом.")
            args.length = int(length)
    return args


def interactive(app: PasswordManager, parser: argparse.ArgumentParser) -> None:
    app.just_logged_in = False  # в интерактивном режиме get заново спрашивает мастер-пароль
    while True:
        print_menu()
        try:
            line = input(f"Выберите действие (1-{len(MENU)}): ").strip()
        except EOFError:
            print()
            return
        if not line:
            continue
        if line in EXIT_WORDS:
            print("До свидания!")
            return
        if line in {"help", "?"}:
            parser.print_help()
            continue
        try:
            if line in MENU:
                args = args_from_menu(MENU[line][0])
            elif line.isdigit():
                raise AppError(f"Нет такого пункта. Введите число от 1 до {len(MENU)}.")
            else:
                # можно ввести и команду целиком, например: get Google
                args = parser.parse_args(shlex.split(line))
                if args.command is None:
                    continue
            run_command(app, args)
        except SystemExit:  # argparse уже напечатал ошибку
            continue
        except ValueError as exc:  # незакрытая кавычка в shlex
            print(f"Ошибка: {exc}")
        except AppError as exc:
            print(f"Ошибка: {exc}")
        except (KeyboardInterrupt, EOFError):
            print("\nОтменено.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        app = PasswordManager()
        app.login()
        if args.command is None:
            interactive(app, parser)
        else:
            run_command(app, args)
    except AppError as exc:
        print(f"Ошибка: {exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nОтменено.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
