from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
from xml.etree import ElementTree

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


MAX_CALLBACK_BODY_BYTES = 1024 * 1024


class WeComCallbackError(ValueError):
    pass


class WeComCallbackCrypto:
    def __init__(self, *, token: str, encoding_aes_key: str, receive_id: str) -> None:
        if not token or not encoding_aes_key or not receive_id:
            raise WeComCallbackError("企业微信回调配置不完整")
        try:
            key = base64.b64decode(f"{encoding_aes_key}=", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise WeComCallbackError("EncodingAESKey 格式无效") from exc
        if len(key) != 32:
            raise WeComCallbackError("EncodingAESKey 解码后必须为 32 字节")
        self._token = token
        self._key = key
        self._receive_id = receive_id

    def signature(self, timestamp: str, nonce: str, encrypted: str) -> str:
        parts = sorted((self._token, timestamp, nonce, encrypted))
        return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()

    def verify_signature(
        self, *, signature: str, timestamp: str, nonce: str, encrypted: str
    ) -> None:
        expected = self.signature(timestamp, nonce, encrypted)
        if not hmac.compare_digest(expected, signature):
            raise WeComCallbackError("企业微信回调签名校验失败")

    def decrypt(self, encrypted: str) -> str:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
            decryptor = Cipher(
                algorithms.AES(self._key), modes.CBC(self._key[:16])
            ).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        except Exception as exc:
            raise WeComCallbackError("企业微信回调密文解密失败") from exc
        if len(plaintext) < 20:
            raise WeComCallbackError("企业微信回调明文长度无效")
        message_length = struct.unpack("!I", plaintext[16:20])[0]
        end = 20 + message_length
        if end > len(plaintext):
            raise WeComCallbackError("企业微信回调消息长度无效")
        try:
            message = plaintext[20:end].decode("utf-8")
            receive_id = plaintext[end:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WeComCallbackError("企业微信回调明文编码无效") from exc
        if receive_id != self._receive_id:
            raise WeComCallbackError("企业微信回调接收方不匹配")
        return message

    def verify_url(
        self, *, signature: str, timestamp: str, nonce: str, echo_str: str
    ) -> str:
        self.verify_signature(
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=echo_str,
        )
        return self.decrypt(echo_str)

    def decrypt_xml(
        self, *, signature: str, timestamp: str, nonce: str, body: bytes
    ) -> str:
        if len(body) > MAX_CALLBACK_BODY_BYTES:
            raise WeComCallbackError("企业微信回调请求体过大")
        try:
            root = ElementTree.fromstring(body)
            encrypted = root.findtext("Encrypt")
        except ElementTree.ParseError as exc:
            raise WeComCallbackError("企业微信回调 XML 无效") from exc
        if not encrypted:
            raise WeComCallbackError("企业微信回调缺少 Encrypt 字段")
        self.verify_signature(
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=encrypted,
        )
        return self.decrypt(encrypted)
