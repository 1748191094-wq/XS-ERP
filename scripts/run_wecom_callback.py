from __future__ import annotations

import sys
from pathlib import Path

import uvicorn


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings


if __name__ == "__main__":
    uvicorn.run(
        "app.integrations.wecom.callback_app:app",
        host=settings.wecom_callback_host,
        port=settings.wecom_callback_port,
        access_log=False,
    )
