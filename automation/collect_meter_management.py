#!/usr/bin/env python3
"""Collect and sanitize WTYZ electricity-meter exceptions for the dashboard."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_BASE_URL = "http://wtyz.hzbeiyang.com"
DEFAULT_PROJECT_ID = "48452"
LOGIN_PATH = "/ruoyi-bb/platform/auth/manager/login"
DEVICE_PATH = "/ruoyi-bb/platform/device/getMeterData"


class CollectionError(RuntimeError):
    pass


def now_text() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    token: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": base_url,
        "Referer": base_url + "/",
        "User-Agent": "YuxiaorMeterCollector/1.0",
    }
    if token:
        headers["Authorization"] = token
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise CollectionError(f"HTTP {exc.code} at {path}") from exc
    except urllib.error.URLError as exc:
        raise CollectionError(f"Network error at {path}: {exc.reason}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectionError(f"Non-JSON response at {path}") from exc
    if not isinstance(result, dict):
        raise CollectionError(f"Unexpected response type at {path}")
    code = result.get("code")
    if code not in (None, 0, 200, "0", "200"):
        message = str(result.get("msg") or result.get("message") or "request rejected")
        raise CollectionError(f"API {code} at {path}: {message[:160]}")
    return result


def find_token(result: dict[str, Any]) -> str:
    candidates: list[Any] = [
        result.get("access_token"),
        result.get("accessToken"),
        result.get("token"),
    ]
    data = result.get("data")
    if isinstance(data, dict):
        candidates.extend(
            (data.get("access_token"), data.get("accessToken"), data.get("token"))
        )
    token = next((str(value).strip() for value in candidates if value), "")
    if not token:
        raise CollectionError("Login response did not contain an access token")
    return token


def extract_page(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    containers: list[dict[str, Any]] = [result]
    data = result.get("data")
    if isinstance(data, dict):
        containers.insert(0, data)
        nested = data.get("data")
        if isinstance(nested, dict):
            containers.insert(0, nested)
    rows: list[dict[str, Any]] | None = None
    total: int | None = None
    for container in containers:
        for key in ("rows", "records", "list", "items"):
            value = container.get(key)
            if isinstance(value, list):
                rows = [row for row in value if isinstance(row, dict)]
                break
        if total is None:
            for key in ("total", "totalCount", "count"):
                value = container.get(key)
                if value is not None:
                    try:
                        total = int(value)
                    except (TypeError, ValueError):
                        pass
                    break
        if rows is not None:
            break
    if rows is None:
        raise CollectionError("Device response did not contain a row list")
    return rows, total


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def sanitize(row: dict[str, Any]) -> dict[str, Any]:
    relay = str(row.get("jdqzt") if row.get("jdqzt") is not None else "")
    power_label = {"0": "通电", "1": "断电"}.get(relay, "未知")
    return {
        "deviceId": str(row.get("measureNo") or row.get("measureId") or "").strip(),
        "deviceName": str(row.get("measureName") or "未命名设备").strip(),
        "areaName": str(row.get("containerName") or "未分区").strip(),
        "onlineStatus": str(row.get("onlineStatus") or "未知").strip(),
        "powerStatus": power_label,
        "remainingPower": number(
            row.get("remainingPowerNew")
            if row.get("remainingPowerNew") not in (None, "")
            else row.get("remainingPower")
        ),
        "updatedAt": str(row.get("remainingPowerTime") or "").strip(),
        "keepElectric": str(row.get("keepElecFlag") or "0") == "1",
    }


def fetch_once(args: argparse.Namespace) -> dict[str, Any]:
    login = post_json(
        args.base_url,
        LOGIN_PATH,
        {
            "deviceId": "",
            "password": args.password,
            "phoneType": "",
            "staffNo": args.username,
            "xcxAppId": args.xcx_app_id,
        },
        timeout=args.timeout,
    )
    token = find_token(login)
    rows: list[dict[str, Any]] = []
    expected_total: int | None = None
    page_number = 1
    while True:
        result = post_json(
            args.base_url,
            DEVICE_PATH,
            {
                "projectId": args.project_id,
                "measureNo": "",
                "measureType": "1",
                "gatewayNo": "",
                "gatewayId": "",
                "containerId": "",
                "tmplId": "",
                "sortOrder": "measureName",
                "dir": "asc",
                "jdqzt": "",
                "meterShareType": "",
                "pageSize": args.page_size,
                "pageNumber": page_number,
            },
            token=token,
            timeout=args.timeout,
        )
        page_rows, page_total = extract_page(result)
        if expected_total is None and page_total is not None:
            expected_total = page_total
        rows.extend(page_rows)
        if not page_rows:
            break
        if expected_total is not None and len(rows) >= expected_total:
            break
        if len(page_rows) < args.page_size:
            break
        page_number += 1
        if page_number > args.max_pages:
            raise CollectionError(f"Device pagination exceeded {args.max_pages} pages")

    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("measureNo") or row.get("measureId") or "").strip()
        if not key:
            raise CollectionError("Device row is missing both measureNo and measureId")
        unique[key] = row
    if expected_total is None:
        expected_total = len(unique)
    if expected_total <= 0 or len(unique) != expected_total:
        raise CollectionError(
            f"Incomplete device collection: expected {expected_total}, got {len(unique)} unique rows"
        )

    devices = [sanitize(row) for row in unique.values()]
    offline = [row for row in devices if row["onlineStatus"] == "离线"]
    negative = [
        row
        for row in devices
        if row["remainingPower"] is not None and row["remainingPower"] < 0
    ]
    keep_electric = [row for row in devices if row["keepElectric"]]
    offline.sort(key=lambda row: (row["updatedAt"] or "", row["deviceName"]), reverse=True)
    negative.sort(key=lambda row: (row["remainingPower"], row["deviceName"]))
    keep_electric.sort(
        key=lambda row: (row["updatedAt"] or "", row["deviceName"]), reverse=True
    )
    online_count = sum(row["onlineStatus"] == "在线" for row in devices)
    collected_at = now_text()
    return {
        "schemaVersion": 1,
        "source": "微亭易租设备管理",
        "projectId": args.project_id,
        "collectedAt": collected_at,
        "summary": {
            "total": len(devices),
            "online": online_count,
            "offline": len(offline),
            "negative": len(negative),
            "keepElectric": len(keep_electric),
        },
        "keepElectricDevices": keep_electric,
        "negativeDevices": negative,
        "offlineDevices": offline,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("WTYZ_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--project-id", default=os.environ.get("WTYZ_PROJECT_ID", DEFAULT_PROJECT_ID))
    parser.add_argument("--username", default=os.environ.get("WTYZ_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("WTYZ_PASSWORD", ""))
    parser.add_argument("--xcx-app-id", default=os.environ.get("WTYZ_XCX_APP_ID", "wx15e9d82e1636f01f"))
    parser.add_argument("--output-dir", type=Path, default=Path(os.environ.get("WTYZ_OUTPUT_DIR", "/home/ubuntu/wtyz-meter-collector/data")))
    parser.add_argument("--attempts", type=int, default=int(os.environ.get("WTYZ_ATTEMPTS", "2")))
    parser.add_argument("--retry-delay", type=int, default=int(os.environ.get("WTYZ_RETRY_DELAY", "60")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("WTYZ_TIMEOUT", "30")))
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.username or not args.password:
        print("WTYZ_USERNAME and WTYZ_PASSWORD are required", file=sys.stderr)
        return 2
    if args.attempts != 2:
        print("WTYZ_ATTEMPTS must remain 2 (initial collection plus one retry)", file=sys.stderr)
        return 2
    started = time.monotonic()
    last_error = ""
    for attempt in range(1, args.attempts + 1):
        try:
            payload = fetch_once(args)
            payload["attemptsUsed"] = attempt
            payload["durationSeconds"] = round(time.monotonic() - started, 2)
            atomic_json(args.output_dir / "latest.json", payload)
            atomic_json(
                args.output_dir / "status.json",
                {
                    "state": "ok",
                    "collectedAt": payload["collectedAt"],
                    "attemptsUsed": attempt,
                    "durationSeconds": payload["durationSeconds"],
                },
            )
            print(
                json.dumps(
                    {"state": "ok", **payload["summary"], "attemptsUsed": attempt},
                    ensure_ascii=False,
                )
            )
            return 0
        except Exception as exc:  # keep last good data and expose only a sanitized failure status
            last_error = str(exc).replace(args.password, "***")[:300]
            print(f"meter collection attempt {attempt}/{args.attempts} failed: {last_error}", file=sys.stderr)
            if attempt < args.attempts:
                time.sleep(args.retry_delay)
    atomic_json(
        args.output_dir / "status.json",
        {
            "state": "failed",
            "failedAt": now_text(),
            "attemptsUsed": args.attempts,
            "durationSeconds": round(time.monotonic() - started, 2),
            "error": last_error,
        },
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
