from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
import platform
import secrets
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class SecretVault:
    """Protect local secrets with Windows DPAPI; authenticated fallback for other systems."""

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data)
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    @classmethod
    def _windows_encrypt(cls, plaintext: bytes) -> bytes:
        source, source_buffer = cls._blob(plaintext)
        output = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptProtectData(ctypes.byref(source), "Service SMTP", None, None, None, 0, ctypes.byref(output)):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    @classmethod
    def _windows_decrypt(cls, ciphertext: bytes) -> bytes:
        source, source_buffer = cls._blob(ciphertext)
        output = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    @staticmethod
    def _fallback_key() -> bytes:
        material = os.getenv("LOCAL_SECRET_KEY") or f"{platform.node()}|SRV-Repair-Local-Vault-2026"
        return hashlib.sha256(material.encode("utf-8")).digest()

    @classmethod
    def _fallback_encrypt(cls, plaintext: bytes) -> bytes:
        key, nonce = cls._fallback_key(), secrets.token_bytes(16)
        stream = bytearray()
        counter = 0
        while len(stream) < len(plaintext):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
            counter += 1
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
        tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        return nonce + tag + ciphertext

    @classmethod
    def _fallback_decrypt(cls, payload: bytes) -> bytes:
        nonce, tag, ciphertext = payload[:16], payload[16:48], payload[48:]
        key = cls._fallback_key()
        expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("secret integrity check failed")
        stream = bytearray()
        counter = 0
        while len(stream) < len(ciphertext):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
            counter += 1
        return bytes(a ^ b for a, b in zip(ciphertext, stream))

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        if not plaintext:
            return ""
        raw = plaintext.encode("utf-8")
        if os.name == "nt":
            return "dpapi:" + base64.b64encode(cls._windows_encrypt(raw)).decode("ascii")
        return "local:" + base64.b64encode(cls._fallback_encrypt(raw)).decode("ascii")

    @classmethod
    def decrypt(cls, encoded: str) -> str:
        if not encoded:
            return ""
        prefix, payload = encoded.split(":", 1)
        raw = base64.b64decode(payload.encode("ascii"))
        if prefix == "dpapi" and os.name == "nt":
            return cls._windows_decrypt(raw).decode("utf-8")
        if prefix == "local":
            return cls._fallback_decrypt(raw).decode("utf-8")
        raise ValueError("unsupported local secret format")
