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
        "allDevices": devices,
    }


def load_accounts(args: argparse.Namespace) -> list[dict[str, str]]:
    """Load credentials without ever copying them into generated snapshots."""
    if args.accounts_file.is_file():
        loaded = json.loads(args.accounts_file.read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or not loaded:
            raise CollectionError("WTYZ accounts file must contain a non-empty list")
        accounts = loaded
    else:
        accounts = [{
            "key": "primary", "name": "原电表账号", "projectId": args.project_id,
            "username": args.username, "password": args.password,
        }]
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, value in enumerate(accounts, 1):
        if not isinstance(value, dict):
            raise CollectionError(f"Account {index} is not an object")
        account = {name: str(value.get(name) or "").strip() for name in (
            "key", "name", "projectId", "username", "password"
        )}
        if not all(account.values()):
            raise CollectionError(f"Account {index} is missing a required field")
        if not re_fullmatch_key(account["key"]) or account["key"] in seen:
            raise CollectionError(f"Account {index} has an invalid or duplicate key")
        seen.add(account["key"])
        result.append(account)
    return result


def re_fullmatch_key(value: str) -> bool:
    return bool(value) and all(character.isalnum() or character in "-_" for character in value)


def account_args(args: argparse.Namespace, account: dict[str, str]) -> argparse.Namespace:
    values = vars(args).copy()
    values.update(username=account["username"], password=account["password"], project_id=account["projectId"])
    return argparse.Namespace(**values)


def snapshot_path(args: argparse.Namespace, key: str) -> Path:
    return args.output_dir / "accounts" / f"{key}-latest.json"


def add_offline_counts(args: argparse.Namespace, accounts: list[dict[str, Any]], successful: set[str]) -> None:
    state_path = args.output_dir / "offline-history.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {"version": 1, "counts": {}}
    counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
    for account in accounts:
        key = str(account["key"])
        for row in account.get("offlineDevices", []):
            source_key = f'{key}:{row["deviceId"]}'
            if key in successful:
                counts[source_key] = int(counts.get(source_key) or 0) + 1
            row["offlineCount"] = int(counts.get(source_key) or 0)
    atomic_json(state_path, {"version": 1, "updatedAt": now_text(), "counts": counts})


def merge_accounts(accounts: list[dict[str, Any]], status: str) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for account in accounts:
        for row in account.get("allDevices", []):
            sanitized = {key: value for key, value in row.items() if key != "offlineCount"}
            unique.setdefault(row["deviceId"], sanitized)
    devices = list(unique.values())
    offline = sorted((row for row in devices if row["onlineStatus"] == "离线"), key=lambda row: (row["updatedAt"] or "", row["deviceName"]), reverse=True)
    negative = sorted((row for row in devices if row["remainingPower"] is not None and row["remainingPower"] < 0), key=lambda row: (row["remainingPower"], row["deviceName"]))
    keep = sorted((row for row in devices if row["keepElectric"]), key=lambda row: (row["updatedAt"] or "", row["deviceName"]), reverse=True)
    public_accounts=[]
    for account in accounts:
        public_accounts.append({name: account[name] for name in (
            "key", "name", "projectId", "status", "stale", "collectedAt", "summary",
            "keepElectricDevices", "negativeDevices", "offlineDevices"
        )})
    return {
        "schemaVersion": 2, "source": "微亭易租设备管理", "projectId": "multiple",
        "collectedAt": now_text(), "collectionState": status,
        "summary": {"total": len(devices), "online": sum(row["onlineStatus"] == "在线" for row in devices),
                    "offline": len(offline), "negative": len(negative), "keepElectric": len(keep)},
        "keepElectricDevices": keep, "negativeDevices": negative, "offlineDevices": offline,
        "accounts": public_accounts,
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
    parser.add_argument("--accounts-file", type=Path, default=Path(os.environ.get("WTYZ_ACCOUNTS_FILE", "/home/ubuntu/wtyz-meter-collector/config/accounts.json")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.attempts != 2:
        print("WTYZ_ATTEMPTS must remain 2 (initial collection plus one retry)", file=sys.stderr)
        return 2
    started = time.monotonic()
    configs = load_accounts(args)
    snapshots: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    successful: set[str] = set()
    for config in configs:
        error = ""
        current = None
        for attempt in range(1, args.attempts + 1):
            try:
                current = fetch_once(account_args(args, config))
                current.update(key=config["key"], name=config["name"], status="ok", stale=False, attemptsUsed=attempt)
                atomic_json(snapshot_path(args, config["key"]), current)
                successful.add(config["key"])
                break
            except Exception as exc:
                error = str(exc).replace(config["password"], "***")[:300]
                if attempt < args.attempts:
                    time.sleep(args.retry_delay)
        if current is None:
            try:
                current = json.loads(snapshot_path(args, config["key"]).read_text(encoding="utf-8"))
                current.update(key=config["key"], name=config["name"], status="failed", stale=True)
            except (OSError, json.JSONDecodeError):
                current = {"key": config["key"], "name": config["name"], "projectId": config["projectId"], "status": "failed", "stale": True, "collectedAt": "", "summary": {"total": 0, "online": 0, "offline": 0, "negative": 0, "keepElectric": 0}, "keepElectricDevices": [], "negativeDevices": [], "offlineDevices": [], "allDevices": []}
        snapshots.append(current)
        outcomes.append({"key": config["key"], "name": config["name"], "projectId": config["projectId"], "state": current["status"], "stale": current["stale"], "attemptsUsed": current.get("attemptsUsed", args.attempts), **({"error": error} if error else {})})
    add_offline_counts(args, snapshots, successful)
    state = "ok" if len(successful) == len(configs) else "partial" if successful else "failed"
    payload = merge_accounts(snapshots, state)
    payload["durationSeconds"] = round(time.monotonic() - started, 2)
    atomic_json(args.output_dir / "latest.json", payload)
    atomic_json(args.output_dir / "status.json", {"state": state, "collectedAt": payload["collectedAt"], "durationSeconds": payload["durationSeconds"], "accounts": outcomes})
    print(json.dumps({"state": state, **payload["summary"], "accounts": outcomes}, ensure_ascii=False))
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
