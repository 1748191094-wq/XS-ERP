from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.core.config import BASE_DIR


def configure_logging() -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers.append(
        RotatingFileHandler(log_dir / "repair_management.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
