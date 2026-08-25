from __future__ import annotations

from functools import lru_cache
import threading
import time
from typing import Protocol

import httpx

from app.core.config import settings


TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
MESSAGE_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
TOKEN_ERROR_CODES = {40014, 42001, 42007, 42009}


class WeComAPIError(RuntimeError):
    """A sanitized provider error that never includes credentials or access tokens."""

    def __init__(self, message: str, *, errcode: int | None = None):
        super().__init__(message)
        self.errcode = errcode


class WeComService(Protocol):
    def send_app_text(self, user_id: str, text: str) -> dict: ...


class MockWeComService:
    def send_app_text(self, user_id: str, text: str) -> dict:
        return {
            "provider": "mock_wecom",
            "status": "mock",
            "accepted": True,
            "delivered": False,
            "recipient_userid": user_id,
            "preview": text[:120],
            "message": "企业微信处于 Mock 模式，未向外部发送",
        }


class WeComApplicationService:
    def __init__(
        self,
        *,
        corp_id: str,
        app_secret: str,
        agent_id: str,
        timeout_seconds: int = 15,
        client: httpx.Client | None = None,
    ) -> None:
        if not corp_id or not app_secret or not agent_id:
            raise WeComAPIError("企业微信应用配置不完整")
        try:
            self.agent_id = int(agent_id)
        except (TypeError, ValueError) as exc:
            raise WeComAPIError("企业微信 AgentId 必须是数字") from exc
        self.corp_id = corp_id
        self._app_secret = app_secret
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request_json(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeComAPIError("企业微信网络请求失败或返回内容无效") from exc
        if not isinstance(payload, dict):
            raise WeComAPIError("企业微信返回内容格式无效")
        return payload

    def _get_token(self, *, force: bool = False) -> str:
        now = time.monotonic()
        if not force and self._token and now < self._token_expires_at:
            return self._token
        with self._token_lock:
            now = time.monotonic()
            if not force and self._token and now < self._token_expires_at:
                return self._token
            payload = self._request_json(
                "GET",
                TOKEN_URL,
                params={"corpid": self.corp_id, "corpsecret": self._app_secret},
            )
            errcode = int(payload.get("errcode", -1))
            token = payload.get("access_token")
            if errcode != 0 or not isinstance(token, str) or not token:
                raise WeComAPIError(
                    f"企业微信获取 access_token 失败：{payload.get('errmsg', 'unknown error')}",
                    errcode=errcode,
                )
            expires_in = max(300, int(payload.get("expires_in", 7200)))
            self._token = token
            self._token_expires_at = time.monotonic() + max(60, expires_in - 120)
            return token

    def _send_once(self, token: str, user_id: str, text: str) -> dict:
        return self._request_json(
            "POST",
            MESSAGE_URL,
            params={"access_token": token},
            json={
                "touser": user_id,
                "msgtype": "text",
                "agentid": self.agent_id,
                "text": {"content": text},
                "safe": 0,
                "enable_id_trans": 0,
                "enable_duplicate_check": 1,
                "duplicate_check_interval": 1800,
            },
        )

    def send_app_text(self, user_id: str, text: str) -> dict:
        recipient = user_id.strip()
        content = text.strip()
        if not recipient:
            raise WeComAPIError("企业微信接收人 UserID 不能为空")
        if not content:
            raise WeComAPIError("企业微信消息内容不能为空")
        payload = self._send_once(self._get_token(), recipient, content)
        errcode = int(payload.get("errcode", -1))
        if errcode in TOKEN_ERROR_CODES:
            payload = self._send_once(self._get_token(force=True), recipient, content)
            errcode = int(payload.get("errcode", -1))
        if errcode != 0:
            raise WeComAPIError(
                f"企业微信发送失败：{payload.get('errmsg', 'unknown error')}",
                errcode=errcode,
            )
        return {
            "provider": "wecom_app",
            "status": "sent",
            "accepted": True,
            "delivered": True,
            "recipient_userid": recipient,
            "errcode": 0,
            "message": "企业微信接口已接受消息",
        }


@lru_cache(maxsize=1)
def get_wecom_service() -> WeComService:
    if settings.wecom_mode != "real":
        return MockWeComService()
    return WeComApplicationService(
        corp_id=settings.wecom_corp_id,
        app_secret=settings.wecom_app_secret,
        agent_id=settings.wecom_agent_id,
        timeout_seconds=settings.wecom_timeout_seconds,
    )
