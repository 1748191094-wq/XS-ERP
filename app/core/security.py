from __future__ import annotations

import re
import secrets
import base64
import hashlib
import hmac
from pathlib import Path

from app.core.config import settings


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_MANAGEMENT_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MANAGEMENT_PASSWORD_PATTERN = re.compile(
    r"^[a-z2-9]{4}(?:-[a-z2-9]{4}){3}$"
)


def safe_filename(filename: str) -> str:
    raw = Path(filename or "attachment").name
    cleaned = _SAFE_CHARS.sub("_", raw).strip("._")
    if not cleaned:
        cleaned = "attachment"
    stem, suffix = Path(cleaned).stem[:80], Path(cleaned).suffix[:16].lower()
    return f"{stem}-{secrets.token_hex(4)}{suffix}"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, settings.password_hash_iterations
    )
    return "pbkdf2_sha256${}${}${}".format(
        settings.password_hash_iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def generate_management_password() -> str:
    """Return 16 high-entropy characters grouped for reliable human entry."""

    while True:
        raw = "".join(secrets.choice(_MANAGEMENT_PASSWORD_ALPHABET) for _ in range(16))
        if any(character.isalpha() for character in raw) and any(
            character.isdigit() for character in raw
        ):
            return "-".join(raw[index : index + 4] for index in range(0, 16, 4))


def validate_emergency_password(password: str) -> str:
    if not 16 <= len(password) <= 128:
        raise ValueError("紧急恢复密码长度必须为 16 至 128 位")
    if not any(character.isalpha() for character in password) or not any(
        character.isdigit() for character in password
    ):
        raise ValueError("紧急恢复密码必须同时包含字母和数字")
    return password


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(raw_iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
