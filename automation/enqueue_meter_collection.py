#!/usr/bin/env python3
"""Queue one sanitized two-hour meter collection summary for WeCom."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DATA = Path(os.environ.get("METER_MANAGEMENT_JSON", "/opt/yuxiaor-automation/data/meter-management/latest.json"))
STATUS = Path(os.environ.get("METER_MANAGEMENT_STATUS_JSON", "/opt/yuxiaor-automation/data/meter-management/status.json"))
QUEUE = Path(os.environ.get("METER_NOTIFICATION_QUEUE", "/opt/yuxiaor-automation/notifications/pending"))
REPORT_CHAT_ID = "wrki7WEAAAYzG-hYJ4delzv_Y7Us71ow"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def value(row: dict) -> str:
    amount = row.get("remainingPower")
    return "--" if amount is None else f"{float(amount):,.2f}"


def detail_lines(title: str, rows: list[dict], formatter) -> list[str]:
    lines = [f"**{title}（{len(rows)}台）**"]
    if not rows:
        return lines + ["> 无"]
    return lines + [f"{index}. {formatter(row)}" for index, row in enumerate(rows, 1)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-queue", action="store_true")
    args = parser.parse_args()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    now = datetime.now(SHANGHAI)
    state = str(status.get("state") or data.get("collectionState") or "failed")
    label = {"ok": "全部成功", "partial": "部分成功", "failed": "采集失败"}.get(state, state)
    lines = [
        f"**电表采集简报｜{now:%Y-%m-%d %H:%M}**",
        f">每2小时采集｜总体状态：**{label}**",
    ]
    for account in data.get("accounts", []):
        summary = account.get("summary") or {}
        account_state = "成功" if account.get("status") == "ok" else "失败（展示上次数据）" if account.get("stale") else "失败"
        lines += [
            "",
            f"### {account.get('name') or account.get('key')}｜{account_state}",
            f">设备 {int(summary.get('total') or 0)}｜离线 {int(summary.get('offline') or 0)}｜保电 {int(summary.get('keepElectric') or 0)}｜欠费 {int(summary.get('negative') or 0)}",
        ]
        lines += detail_lines("离线设备", account.get("offlineDevices") or [], lambda row: f"{row.get('deviceName')}｜离线{int(row.get('offlineCount') or 0)}次｜最新采集 {row.get('updatedAt') or '--'}")
        lines += detail_lines("保电设备", account.get("keepElectricDevices") or [], lambda row: f"{row.get('deviceName')}｜余额 {value(row)}")
        lines += detail_lines("欠费设备", account.get("negativeDevices") or [], lambda row: f"{row.get('deviceName')}｜欠费金额 ¥{value(row)}")
    job_id = f"meter-collection-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    job = {"id": job_id, "kind": "meter-collection-summary", "chatid": REPORT_CHAT_ID,
           "createdAt": now.isoformat(timespec="seconds"), "files": [], "summary": "\n".join(lines)}
    if not args.no_queue:
        atomic_json(QUEUE / f"{job_id}.json", job)
    print(json.dumps({"job": job_id, "state": state, "accounts": len(data.get("accounts", [])), "queued": not args.no_queue, "messageLength": len(job["summary"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
