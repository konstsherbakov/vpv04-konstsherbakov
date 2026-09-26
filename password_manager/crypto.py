"""Криптография: ключ Fernet, шифрование паролей, хеш мастер-пароля, генератор паролей."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import string
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_PATH = Path(__file__).resolve().with_name(".key")
SALT_BYTES = 16

__all__ = [
    "KEY_PATH",
    "InvalidToken",
    "key_exists",
    "load_or_create_key",
    "make_fernet",
    "encrypt",
    "decrypt",
    "hash_master",
    "verify_master",
    "generate_password",
]


# ---------- ключ шифрования ----------

def key_exists(path: Path = KEY_PATH) -> bool:
    return path.is_file()


def load_or_create_key(path: Path = KEY_PATH) -> bytes:
    """Читает ключ из файла .key; если файла нет — генерирует и сохраняет новый."""
    if path.is_file():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)  # на Windows влияет только на флаг «только чтение»
    except OSError:
        pass
    return key


def make_fernet(key: bytes) -> Fernet:
    return Fernet(key)


def encrypt(fernet: Fernet, plaintext: str) -> bytes:
    return fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(fernet: Fernet, token: bytes) -> str:
    """Бросает InvalidToken, если ключ не подходит или данные повреждены."""
    return fernet.decrypt(token).decode("utf-8")


# ---------- мастер-пароль ----------

def hash_master(password: str, salt: bytes | None = None) -> tuple[bytes, str]:
    """SHA-256 от соль + пароль. Возвращает (соль, hex-хеш).

    Соль делает одинаковые пароли разными в базе и защищает от готовых
    радужных таблиц.
    """
    if salt is None:
        salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.sha256(salt + password.encode("utf-8")).hexdigest()
    return salt, digest


def verify_master(password: str, salt: bytes, expected_hash: str) -> bool:
    _, digest = hash_master(password, salt)
    # сравнение за постоянное время — не выдаёт по таймингу, сколько символов совпало
    return hmac.compare_digest(digest, expected_hash)


# ---------- генератор паролей ----------

SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?"


def generate_password(length: int = 16, use_symbols: bool = True) -> str:
    """Криптостойкий случайный пароль: минимум одна строчная, заглавная, цифра (и символ)."""
    groups = [string.ascii_lowercase, string.ascii_uppercase, string.digits]
    if use_symbols:
        groups.append(SYMBOLS)
    if length < len(groups):
        raise ValueError(f"Длина пароля должна быть не меньше {len(groups)}")

    alphabet = "".join(groups)
    chars = [secrets.choice(group) for group in groups]
    chars += [secrets.choice(alphabet) for _ in range(length - len(groups))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
