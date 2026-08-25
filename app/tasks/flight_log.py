from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.core.database import SessionLocal
from app.integrations.flight_log.analyzers import AnalyzerEngine
from app.integrations.flight_log.parsers import ParserRegistry, UnsupportedLogError
from app.models.entities import Diagnosis, FlightLog, TaskRecord

logger = logging.getLogger(__name__)


def parse_flight_log_task(flight_log_id: int, task_id: int) -> None:
    with SessionLocal() as db:
        log = db.get(FlightLog, flight_log_id)
        task = db.get(TaskRecord, task_id)
        if not log or not task:
            return
        try:
            now = datetime.now(timezone.utc)
            log.parse_status, log.parse_progress = "parsing", 10
            task.status, task.progress, task.started_at = "running", 10, now
            db.commit()
            parser = ParserRegistry().parser_for(log.storage_path)
            log.parser_name, log.parser_version, log.parse_progress = parser.name, parser.version, 35
            db.commit()
            parsed = parser.parse(log.storage_path)
            log.parsed_data_json, log.parse_progress = parsed.to_dict(), 75
            db.commit()
            for finding in AnalyzerEngine().analyze(log.parsed_data_json):
                db.add(Diagnosis(
                    repair_order_id=log.repair_order_id, flight_log_id=log.id,
                    diagnosis_type=finding.diagnosis_type, severity=finding.severity,
                    confidence=finding.confidence, title=finding.title, description=finding.description,
                    evidence_json=finding.evidence, suggested_actions=finding.suggested_actions,
                    requires_human_confirmation=finding.requires_human_confirmation,
                ))
            log.parse_status, log.parse_progress, log.parsed_at = "parsed", 100, datetime.now(timezone.utc)
            task.status, task.progress, task.finished_at, task.message = "completed", 100, datetime.now(timezone.utc), "解析与规则分析完成"
            db.commit()
        except UnsupportedLogError as exc:
            log.parse_status, log.parse_progress, log.error_message = "unsupported", 100, str(exc)
            task.status, task.progress, task.finished_at, task.message = "unsupported", 100, datetime.now(timezone.utc), str(exc)
            db.commit()
        except Exception as exc:
            logger.exception("飞控日志解析失败")
            log.parse_status, log.error_message = "parse_failed", str(exc)
            task.status, task.finished_at, task.message = "failed", datetime.now(timezone.utc), str(exc)
            db.commit()
