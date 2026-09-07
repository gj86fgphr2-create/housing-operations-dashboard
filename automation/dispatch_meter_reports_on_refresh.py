#!/usr/bin/env python3
"""Send saved project images when an authorized Weixin user says exactly '刷新'."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


RUNTIME_DIR = Path("/home/ubuntu/openclaw-weixin-runtime")
DATABASE = Path("/home/ubuntu/.openclaw/agents/main/agent/openclaw-agent.sqlite")
RECIPIENTS_FILE = RUNTIME_DIR / "config/youmi-recipients.json"
STATE_FILE = RUNTIME_DIR / "state/meter-refresh-dispatch.json"
LOG_FILE = RUNTIME_DIR / "logs/meter-refresh-dispatch.jsonl"
NODE = RUNTIME_DIR / "node-v24.20.0-linux-x64/bin/node"
SENDER = RUNTIME_DIR / "bin/send-weixin-media-with-context.mjs"
REPORT_DIR = RUNTIME_DIR / "reports"
REPORTS = [
    ("youmi-donglian", "有米东联店"),
    ("laicai-apartment", "来财公寓"),
    ("sanlian", "三联"),
    ("shangjiangcheng", "上江城"),
]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def log_event(payload: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        record = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), **payload}
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def latest_rowid(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM session_transcript_fts").fetchone()
    return int(row[0] or 0)


def main() -> int:
    recipients = load_json(RECIPIENTS_FILE, [])
    by_account = {item.get("account"): item for item in recipients if item.get("account")}
    if not by_account:
        log_event({"event": "error", "reason": "recipient-config-missing"})
        return 1
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    state = load_json(STATE_FILE, {})
    if "cursorRow" not in state:
        state["cursorRow"] = latest_rowid(connection)
        atomic_json(STATE_FILE, state)
        log_event({"event": "initialized", "cursorRow": state["cursorRow"]})
        return 0
    cursor = int(state.get("cursorRow") or 0)
    newest = latest_rowid(connection)
    rows = connection.execute(
        """
        SELECT f.rowid, f.session_id, f.timestamp, w.account_id
        FROM session_transcript_fts AS f
        JOIN session_windows AS w ON w.session_id = f.session_id
        WHERE f.rowid > ? AND f.rowid <= ? AND f.role = 'user' AND trim(f.text) = '刷新'
          AND w.channel = 'openclaw-weixin'
        ORDER BY f.timestamp, f.rowid
        """,
        (cursor, newest),
    ).fetchall()
    failures = 0
    for rowid, session_id, timestamp, account_id in rows:
        recipient = by_account.get(account_id)
        if not recipient:
            log_event({"event": "ignored", "reason": "unauthorized-account", "messageRow": rowid})
            continue
        outcomes = []
        for key, display_name in REPORTS:
            image = REPORT_DIR / f"{key}-meter-report.png"
            if not image.exists():
                outcomes.append({"project": key, "sent": False, "reason": "image-missing"})
                failures += 1
                continue
            result = None
            sent = False
            attempts = 0
            for attempts in range(1, 3):
                result = subprocess.run(
                    [str(NODE), str(SENDER), account_id, recipient["target"], str(image)],
                    text=True,
                    capture_output=True,
                    timeout=120,
                )
                sent = result.returncode == 0
                detail = result.stderr or result.stdout
                if sent or "ret=-2" in detail or "prepare failed" in detail:
                    break
                time.sleep(3)
            outcomes.append({
                "project": key,
                "displayName": display_name,
                "sent": sent,
                "attempts": attempts,
                "detail": (result.stderr or result.stdout)[-300:] if result else "not-run",
            })
            if not sent:
                failures += 1
            time.sleep(2)
        log_event({
            "event": "refresh-dispatch",
            "recipient": recipient.get("name"),
            "messageRow": rowid,
            "session": session_id,
            "outcomes": outcomes,
        })
    state["cursorRow"] = max(cursor, newest)
    atomic_json(STATE_FILE, state)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
