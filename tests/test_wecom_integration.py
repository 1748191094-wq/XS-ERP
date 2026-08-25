from __future__ import annotations

import base64
import struct

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from fastapi.testclient import TestClient
import pytest

from app.integrations.wecom.callback import WeComCallbackCrypto, WeComCallbackError
from app.integrations.wecom.callback_app import create_callback_app
from app.integrations.wecom.service import WeComAPIError, WeComApplicationService


CORP_ID = "ww-test-corp"
TOKEN = "callback-token-for-tests"
AES_KEY = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")


def encrypt_callback(message: str) -> str:
    key = base64.b64decode(f"{AES_KEY}=")
    payload = (
        b"0123456789abcdef"
        + struct.pack("!I", len(message.encode("utf-8")))
        + message.encode("utf-8")
        + CORP_ID.encode("utf-8")
    )
    padder = PKCS7(128).padder()
    padded = padder.update(payload) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def test_callback_url_verification_and_signature_rejection():
    encrypted = encrypt_callback("callback-ok")
    crypto = WeComCallbackCrypto(token=TOKEN, encoding_aes_key=AES_KEY, receive_id=CORP_ID)
    signature = crypto.signature("1700000000", "nonce-1", encrypted)
    app = create_callback_app(corp_id=CORP_ID, token=TOKEN, encoding_aes_key=AES_KEY)
    client = TestClient(app)

    response = client.get("/wecom/callback", params={
        "msg_signature": signature,
        "timestamp": "1700000000",
        "nonce": "nonce-1",
        "echostr": encrypted,
    })
    assert response.status_code == 200
    assert response.text == "callback-ok"

    rejected = client.get("/wecom/callback", params={
        "msg_signature": "0" * 40,
        "timestamp": "1700000000",
        "nonce": "nonce-1",
        "echostr": encrypted,
    })
    assert rejected.status_code == 403


def test_callback_decrypt_rejects_wrong_receive_id():
    encrypted = encrypt_callback("callback-ok")
    crypto = WeComCallbackCrypto(
        token=TOKEN,
        encoding_aes_key=AES_KEY,
        receive_id="ww-another-corp",
    )
    with pytest.raises(WeComCallbackError, match="接收方不匹配"):
        crypto.decrypt(encrypted)


def test_application_message_gets_token_and_sends_text():
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={
                "errcode": 0,
                "errmsg": "ok",
                "access_token": "TOKEN_FOR_TEST",
                "expires_in": 7200,
            })
        assert request.url.params.get("access_token") == "TOKEN_FOR_TEST"
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    service = WeComApplicationService(
        corp_id="ww-test",
        app_secret="APP_SECRET_FOR_TEST",
        agent_id="1000002",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = service.send_app_text("owner-userid", "请处理工单")

    assert result["status"] == "sent"
    assert result["recipient_userid"] == "owner-userid"
    assert requests == [("GET", "/cgi-bin/gettoken"), ("POST", "/cgi-bin/message/send")]


def test_application_error_never_includes_secret():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 40013, "errmsg": "invalid corpid"})

    service = WeComApplicationService(
        corp_id="ww-test",
        app_secret="APP_SECRET_MUST_NOT_LEAK",
        agent_id="1000002",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(WeComAPIError) as exc_info:
        service.send_app_text("owner-userid", "请处理工单")
    assert "APP_SECRET_MUST_NOT_LEAK" not in str(exc_info.value)
