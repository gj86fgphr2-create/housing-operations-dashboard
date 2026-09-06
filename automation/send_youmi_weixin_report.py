#!/usr/bin/env python3
"""Render and send the fresh Youmi Donglian meter snapshot to personal WeChat."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


DATA_FILE = Path("/opt/yuxiaor-automation/data/meter-management/latest.json")
STATUS_FILE = Path("/opt/yuxiaor-automation/data/meter-management/status.json")
RUNTIME_DIR = Path("/home/ubuntu/openclaw-weixin-runtime")
STATE_FILE = RUNTIME_DIR / "state/youmi-weixin-report.json"
LOG_FILE = RUNTIME_DIR / "logs/youmi-weixin-report.jsonl"
REPORT_DIR = RUNTIME_DIR / "reports"
OPENCLAW = RUNTIME_DIR / "app/node_modules/.bin/openclaw"
NODE_BIN = RUNTIME_DIR / "node-v24.20.0-linux-x64/bin"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
REPORT_ACCOUNTS = {
    "youmi-donglian": "有米东联店",
    "laicai-apartment": "来财公寓",
    "sanlian": "三联",
    "shangjiangcheng": "上江城",
}
RECIPIENTS_FILE = RUNTIME_DIR / "config/youmi-recipients.json"


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def format_time(value: Any) -> str:
    if not value:
        return "未知"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%m-%d-%H-%M")
    except ValueError:
        return str(value)


def balance(row: dict[str, Any]) -> float:
    try:
        value = float(row.get("remainingPower"))
        return value if math.isfinite(value) else float("inf")
    except (TypeError, ValueError):
        return float("inf")


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
        handle.write(json.dumps({"time": datetime.now().astimezone().isoformat(timespec="seconds"), **payload}, ensure_ascii=False) + "\n")


def render(account: dict[str, Any], display_name: str, image_file: Path) -> None:
    offline = sorted(
        account.get("offlineDevices", []),
        key=lambda row: (-int(row.get("offlineCount") or 0), row.get("updatedAt") or "9999", str(row.get("deviceName") or "")),
    )
    keep = sorted(account.get("keepElectricDevices", []), key=lambda row: (balance(row), str(row.get("deviceName") or "")))
    negative = sorted(account.get("negativeDevices", []), key=lambda row: (balance(row), str(row.get("deviceName") or "")))
    total_height = 264 + sum(112 + len(rows) * 48 + 40 for rows in (offline, keep, negative)) + 75
    image = Image.new("RGB", (1000, total_height), "#eef3f7")
    draw = ImageDraw.Draw(image)

    def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)

    def text(x: int, y: int, value: Any, size: int = 24, color: str = "#18334b", bold: bool = False) -> None:
        draw.text((x, y), str(value), font=font(size, bold), fill=color)

    draw.rounded_rectangle((30, 30, 970, 240), 24, fill="#143a4b")
    text(64, 52, "电表设备简报", 22, "#90cbd1")
    text(64, 90, display_name, 46, "white", True)
    text(64, 166, "数据采集  " + format_time(account.get("collectedAt")), 24, "#d4e9ee")
    summary = account.get("summary", {})
    text(655, 95, summary.get("total", 0), 48, "white", True)
    text(782, 119, "台设备", 23, "#d4e9ee")
    text(655, 168, f'在线 {summary.get("online", 0)}   /   离线 {summary.get("offline", 0)}', 22, "#d4e9ee")
    y = 264

    def section(title: str, rows: list[dict[str, Any]], color: str, is_offline: bool = False) -> None:
        nonlocal y
        height = 112 + len(rows) * 48 + 20
        draw.rounded_rectangle((30, y, 970, y + height), 20, fill="white")
        draw.rounded_rectangle((54, y + 25, 61, y + 56), 3, fill=color)
        text(77, y + 22, title, 28, bold=True)
        text(806, y + 26, f"{len(rows)} 台", 25, color, True)
        yy = y + 72
        draw.rectangle((50, yy, 950, yy + 38), fill="#f0f5f8")
        headings = ((67, "序号"), (150, "设备名称"), (445, "离线次数"), (630, "最新采集时间")) if is_offline else ((67, "序号"), (150, "设备名称"), (780, "余额"))
        for x, label in headings:
            text(x, yy + 5, label, 20, "#627889")
        yy += 42
        for index, row in enumerate(rows, 1):
            if index % 2 == 0:
                draw.rectangle((50, yy - 2, 950, yy + 43), fill="#f8fafc")
            text(75, yy + 4, f"{index:02}", 22, "#8295a4")
            text(150, yy + 4, row.get("deviceName", "未知设备"), 23, bold=True)
            if is_offline:
                text(485, yy + 4, row.get("offlineCount", "—"), 23, color, True)
                text(630, yy + 5, format_time(row.get("updatedAt")), 22)
            else:
                value = balance(row)
                display = "—" if not math.isfinite(value) else f"{value:.2f}"
                text(780, yy + 4, display, 24, "#cc4148" if value < 0 else "#18334b", True)
            yy += 48
        y += height + 20

    section("01  离线设备", offline, "#d28a26", True)
    section("02  保电设备", keep, "#168b87")
    section("03  欠费设备", negative, "#cc4148")
    text(50, y + 1, "* 离线次数按每次实际采集发现离线累计；跳过的时段不累加。", 19, "#6b8090")
    text(50, y + 34, "余额保留平台原值；负数代表欠费，保电与欠费可能重叠。", 19, "#6b8090")
    image_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = image_file.with_suffix(".tmp.png")
    image.crop((0, 0, 1000, y + 85)).save(temporary)
    os.replace(temporary, image_file)


def main() -> int:
    latest = load_json(DATA_FILE, {})
    status = load_json(STATUS_FILE, {})
    state = load_json(STATE_FILE, {"recipients": {}})
    recipients = load_json(RECIPIENTS_FILE, [])
    if not recipients:
        log_event({"event": "error", "reason": "recipient-config-missing"})
        return 1
    environment = os.environ.copy()
    environment["PATH"] = str(NODE_BIN) + os.pathsep + environment.get("PATH", "")
    failures = 0
    sent_any = False
    outcomes = {item.get("key"): item for item in status.get("accounts", [])}
    accounts = {item.get("key"): item for item in latest.get("accounts", [])}
    for account_key, display_name in REPORT_ACCOUNTS.items():
        outcome = outcomes.get(account_key)
        if not outcome or not outcome.get("collectedThisRun"):
            continue
        if outcome.get("state") != "ok":
            log_event({"event": "skip", "account": account_key, "reason": "collection-failed"})
            continue
        account = accounts.get(account_key)
        if not account:
            log_event({"event": "error", "account": account_key, "reason": "account-snapshot-missing"})
            failures += 1
            continue
        collected_at = str(account.get("collectedAt") or "")
        image_file = REPORT_DIR / f"{account_key}-meter-report.png"
        render(account, display_name, image_file)
        for recipient in recipients:
            name = recipient["name"]
            channel_account = recipient["account"]
            target = recipient["target"]
            state_key = f"{account_key}:{name}"
            if state.get("recipients", {}).get(state_key) == collected_at:
                continue
            result = None
            sent = False
            for attempt in range(1, 4):
                result = subprocess.run(
                    [str(OPENCLAW), "message", "send", "--channel", "openclaw-weixin", "--account", channel_account, "--target", target, "--media", str(image_file), "--json"],
                    env=environment, text=True, capture_output=True, timeout=120,
                )
                sent = result.returncode == 0
                if sent:
                    break
                if attempt < 3:
                    time.sleep(5)
            sent_any = sent_any or sent
            log_event({"event": "send", "account": account_key, "recipient": name, "collectedAt": collected_at, "sent": sent, "attempts": attempt, "detail": (result.stderr or result.stdout)[-500:]})
            if sent:
                state.setdefault("recipients", {})[state_key] = collected_at
                atomic_json(STATE_FILE, state)
            else:
                failures += 1
    if not sent_any and not failures:
        log_event({"event": "skip", "reason": "no-report-account-collected-this-run"})
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
