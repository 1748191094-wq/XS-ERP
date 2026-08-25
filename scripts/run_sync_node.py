from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal, create_schema
from app.models.entities import SyncEntityState, SyncOutboxEvent, SystemSetting, utcnow
from app.services.sync import (
    PULL_CURSOR_KEY,
    apply_changes,
    collect_local_changes,
    node_id,
    pending_events,
)


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    if not settings.sync_host_url:
        raise RuntimeError("未配置 SYNC_HOST_URL")
    if len(settings.sync_shared_secret) < 24:
        raise RuntimeError("SYNC_SHARED_SECRET 必须至少 24 位")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        f"{settings.sync_host_url}{path}",
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Sync-Secret": settings.sync_shared_secret,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"同步主机返回 HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接同步主机：{exc.reason}") from exc
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "同步主机返回失败")
    return result["data"]


def _cursor(db) -> tuple[SystemSetting | None, int]:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == PULL_CURSOR_KEY))
    try:
        return row, int(row.value) if row else 0
    except ValueError:
        return row, 0


def run_once() -> dict:
    if settings.sync_role != "terminal":
        raise RuntimeError("run_sync_node.py 仅用于 SYNC_ROLE=terminal 的终端")
    create_schema()
    with SessionLocal() as db:
        collection = collect_local_changes(db)
        current_node_id = node_id(db)
        events = pending_events(db)
        push_payload = {
            "node_id": current_node_id,
            "events": [
                {
                    "event_id": item.event_id,
                    "entity_type": item.entity_type,
                    "record_key": item.record_key,
                    "operation": item.operation,
                    "base_revision": item.base_revision,
                    "base_payload_json": item.base_payload_json,
                    "payload_json": item.payload_json,
                    "payload_hash": item.payload_hash,
                }
                for item in events
            ],
        }
        pushed = _request("POST", "/api/sync/push", push_payload)
        by_event = {item.event_id: item for item in events}
        for item in pushed["acknowledgements"]:
            event = by_event.get(item["event_id"])
            if event:
                event.status = "acknowledged"
                event.acknowledged_at = utcnow()
                state = db.scalar(select(SyncEntityState).where(
                    SyncEntityState.entity_type == event.entity_type,
                    SyncEntityState.record_key == event.record_key,
                ))
                if state:
                    state.payload_hash = event.payload_hash
                    state.payload_json = event.payload_json
                    state.server_revision = item["revision"]
                else:
                    db.add(SyncEntityState(
                        entity_type=event.entity_type,
                        record_key=event.record_key,
                        payload_hash=event.payload_hash,
                        payload_json=event.payload_json,
                        server_revision=item["revision"],
                    ))
        for item in pushed["conflicts"]:
            event = by_event.get(item["event_id"])
            if event:
                event.status = "conflict"
                event.error_message = item.get("error") or f"主机冲突编号：{item['conflict_id']}"
        cursor_row, cursor = _cursor(db)
        db.commit()

        pulled_total = applied_total = conflict_total = 0
        while True:
            pulled = _request("GET", f"/api/sync/pull?after={cursor}&limit=1000")
            changes = pulled["changes"]
            pulled_total += len(changes)
            if changes:
                result = apply_changes(db, changes)
                applied_total += result["applied"]
                conflict_total += result["conflicts"]
            cursor = pulled["cursor"]
            cursor_row, _ = _cursor(db)
            if cursor_row:
                cursor_row.value = str(cursor)
            else:
                cursor_row = SystemSetting(
                    key=PULL_CURSOR_KEY,
                    value=str(cursor),
                    description="已从同步主机拉取的最后变更序号",
                    is_secret=False,
                )
                db.add(cursor_row)
            db.commit()
            if not pulled["has_more"]:
                break
        return {
            "node_id": current_node_id,
            "collected": collection,
            "pushed": len(events),
            "acknowledged": len(pushed["acknowledgements"]),
            "push_conflicts": len(pushed["conflicts"]),
            "pulled": pulled_total,
            "applied": applied_total,
            "pull_conflicts": conflict_total,
            "cursor": cursor,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="多端离线同步代理")
    parser.add_argument("--watch", action="store_true", help="持续运行并按配置周期同步")
    parser.add_argument("--interval", type=int, help="覆盖同步间隔秒数，最少 30 秒")
    args = parser.parse_args()
    interval = max(30, args.interval or settings.sync_interval_seconds)
    while True:
        try:
            print(json.dumps(run_once(), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"同步失败：{exc}", file=sys.stderr)
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
