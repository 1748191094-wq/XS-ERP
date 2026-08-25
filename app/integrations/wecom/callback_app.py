from __future__ import annotations

import logging

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import settings
from app.integrations.wecom.callback import WeComCallbackCrypto, WeComCallbackError


logger = logging.getLogger("service.wecom.callback")


def create_callback_app(
    *, corp_id: str, token: str, encoding_aes_key: str
) -> FastAPI:
    app = FastAPI(
        title="企业微信回调网关",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def crypto() -> WeComCallbackCrypto:
        return WeComCallbackCrypto(
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id=corp_id,
        )

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "wecom_callback",
            "configured": bool(corp_id and token and encoding_aes_key),
        }

    @app.get("/wecom/callback", response_class=PlainTextResponse)
    def verify_callback(
        msg_signature: str = Query(min_length=40, max_length=40),
        timestamp: str = Query(min_length=1, max_length=24),
        nonce: str = Query(min_length=1, max_length=128),
        echostr: str = Query(min_length=1, max_length=4096),
    ) -> PlainTextResponse:
        try:
            echo = crypto().verify_url(
                signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                echo_str=echostr,
            )
        except WeComCallbackError as exc:
            logger.warning("WeCom callback URL verification rejected: %s", exc)
            return PlainTextResponse(str(exc), status_code=403)
        return PlainTextResponse(echo)

    @app.post("/wecom/callback", response_class=PlainTextResponse)
    async def receive_callback(
        request: Request,
        msg_signature: str = Query(min_length=40, max_length=40),
        timestamp: str = Query(min_length=1, max_length=24),
        nonce: str = Query(min_length=1, max_length=128),
    ) -> PlainTextResponse:
        try:
            crypto().decrypt_xml(
                signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                body=await request.body(),
            )
        except WeComCallbackError as exc:
            logger.warning("WeCom callback message rejected: %s", exc)
            return PlainTextResponse(str(exc), status_code=403)
        # 当前阶段只完成可信回调验证；业务事件入库在微信客服会话接入阶段启用。
        return PlainTextResponse("success")

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"status": "error"})

    return app


app = create_callback_app(
    corp_id=settings.wecom_corp_id,
    token=settings.wecom_callback_token,
    encoding_aes_key=settings.wecom_callback_aes_key,
)
