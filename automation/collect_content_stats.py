#!/usr/bin/env python3
"""Collect note-performance data from Xiaohongshu's two authenticated consoles.

The collector runs entirely on the cloud server against the existing isolated
browser profiles. It does not call Codex, OpenAI, or any metered AI API.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import io
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from collect_lead_stats import (
    ACCOUNT_MAP,
    PROJECT_DIR,
    TIME_ZONE,
    WebDriverClient,
    atomic_write_text,
    read_accounts,
    run_accountctl,
)


PRO_HOME_URL = "https://pro.xiaohongshu.com/enterprise/home"
CREATOR_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "data" / "content-stats"
LOCK_PATH = PROJECT_DIR / "data" / "content-stats.lock"
CREATOR_NOTE_LIST_PATH = "/api/galaxy/creator/datacenter/note/analyze/list"

CLICK_VISIBLE_TEXT_JS = r"""
const wanted = arguments[0];
const candidates = Array.from(document.querySelectorAll('button,div,span'))
  .filter((el) => (el.textContent || '').trim() === wanted)
  .filter((el) => el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden');
const target = candidates.find((el) => getComputedStyle(el).cursor === 'pointer') || candidates[0];
if (!target) return false;
target.click();
return true;
"""

READ_ACCOUNT_META_JS = r"""
const text = document.body.innerText;
const nameMatch = text.match(/^([^\r\n]+)\r?\n小红书号[：:]/m);
const idMatch = text.match(/小红书号[：:]\s*([^\r\n]+)/);
return {
  accountName: nameMatch ? nameMatch[1].trim() : '',
  xhsId: idMatch ? idMatch[1].trim() : '',
};
"""

READ_PRO_TREND_JS = r"""
const xhr = new XMLHttpRequest();
xhr.open('GET', '/api/eros/business_data/goods_note/seller/trend?noteDataType=2&dateType=3&orderField=readNum', false);
xhr.send();
return { status: xhr.status, text: xhr.responseText };
"""

SET_CREATOR_DATE_RANGE_JS = r"""
const inputs = Array.from(document.querySelectorAll('input.d-text'))
  .filter((el) => el.type === 'text')
  .filter((el) => ['开始时间', '结束时间'].includes(el.placeholder) || /^\d{4}-\d{2}-\d{2}$/.test(el.value))
  .slice(0, 2);
if (inputs.length < 2) return { ok: false, found: inputs.length };
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
for (const [index, value] of [arguments[0], arguments[1]].entries()) {
  setter.call(inputs[index], value);
  inputs[index].dispatchEvent(new Event('input', { bubbles: true }));
  inputs[index].dispatchEvent(new Event('change', { bubbles: true }));
  inputs[index].dispatchEvent(new Event('blur', { bubbles: true }));
}
document.body.click();
return { ok: true, values: inputs.map((el) => el.value) };
"""

READ_CREATOR_ROWS_JS = r"""
return Array.from(document.querySelectorAll('tbody tr'))
  .map((row) => {
    const cells = Array.from(row.querySelectorAll('td')).map((cell) => (cell.innerText || '').trim());
    const sources = [
      ...Array.from(row.querySelectorAll('a[href]')).map((link) => link.href || link.getAttribute('href') || ''),
      ...Array.from(row.attributes)
        .filter((attr) => /note|item|row.*key/i.test(attr.name))
        .map((attr) => attr.value),
      row.outerHTML,
    ];
    const patterns = [
      /(?:explore|discovery\/item|note)\/([0-9a-f]{24})(?:[/?#]|$)/i,
      /(?:noteId|note_id|itemId|item_id|row-key)[^0-9a-f]{0,24}([0-9a-f]{24})/i,
    ];
    let noteId = '';
    for (const source of sources) {
      for (const pattern of patterns) {
        const match = String(source || '').match(pattern);
        if (match) { noteId = match[1].toLowerCase(); break; }
      }
      if (noteId) break;
    }
    return { cells, noteId };
  })
  .filter((row) => row.cells.length >= 4 && /发布于\d{4}-\d{2}-\d{2}/.test(row.cells[0] || ''));
"""

READ_CREATOR_PAGES_JS = r"""
return Array.from(document.querySelectorAll('.d-pagination-page-content'))
  .filter((el) => el.offsetParent !== null)
  .map((el) => Number.parseInt((el.textContent || '').trim(), 10))
  .filter(Number.isFinite);
"""

CLICK_CREATOR_PAGE_JS = r"""
const target = Array.from(document.querySelectorAll('.d-pagination-page-content'))
  .find((el) => el.offsetParent !== null && (el.textContent || '').trim() === String(arguments[0]));
if (!target) return false;
target.click();
return true;
"""

INSTALL_CREATOR_RESPONSE_CAPTURE_JS = r"""
if (!window.__xhsNoteCaptureInstalled) {
  window.__xhsNoteCaptureInstalled = true;
  window.__xhsNoteResponses = [];
  const target = '/api/galaxy/creator/datacenter/note/analyze/list';
  const record = (url, value) => {
    if (!String(url || '').includes(target)) return;
    try {
      const payload = typeof value === 'string' ? JSON.parse(value) : value;
      if (payload) window.__xhsNoteResponses.push(payload);
    } catch (_) {}
  };
  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    const response = await originalFetch.apply(this, args);
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url;
    if (String(url || '').includes(target)) {
      response.clone().text().then((text) => record(url, text)).catch(() => {});
    }
    return response;
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__xhsCaptureUrl = url;
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function(...args) {
    if (String(this.__xhsCaptureUrl || '').includes(target)) {
      this.addEventListener('load', () => {
        try {
          const value = this.responseType === '' || this.responseType === 'text' ? this.responseText : this.response;
          record(this.__xhsCaptureUrl, value);
        } catch (_) {}
      }, {once: true});
    }
    return originalSend.apply(this, args);
  };
}
return true;
"""

READ_CREATOR_RESPONSES_JS = r"""
const responses = Array.isArray(window.__xhsNoteResponses) ? window.__xhsNoteResponses.slice() : [];
window.__xhsNoteResponses = [];
return responses;
"""


def create_content_session(client: WebDriverClient) -> str:
    created = client.request(
        "/session",
        "POST",
        {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "chrome",
                    "pageLoadStrategy": "none",
                    "goog:chromeOptions": {
                        "args": [
                            "--user-data-dir=/home/seluser/.config/chromium",
                            "--no-first-run",
                            "--no-default-browser-check",
                            "--disable-notifications",
                        ]
                    },
                }
            }
        },
    )
    return str(created["sessionId"])


def read_creator_note_ids(client: WebDriverClient, session_id: str) -> dict[tuple[str, str], str]:
    note_ids: dict[tuple[str, str], str] = {}
    responses = client.execute(session_id, READ_CREATOR_RESPONSES_JS)
    for payload in responses:
        try:
            for note in payload.get("data", {}).get("note_infos", []):
                note_id = str(note.get("id") or "").strip().lower()
                title = str(note.get("title") or "").strip()
                post_time = int(note.get("post_time") or 0)
                if not note_id or not title or not post_time:
                    continue
                published_at = dt.datetime.fromtimestamp(post_time / 1000, TIME_ZONE).strftime("%Y-%m-%d %H:%M")
                note_ids[(title, published_at)] = note_id
        except Exception as exc:
            print(f"  creator: warning: unable to parse one captured note-list response: {exc}", file=sys.stderr, flush=True)
    return note_ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", help="Creator-note publish start date; default: previous month day 1")
    parser.add_argument("--end-date", help="Creator-note publish end date; default: today")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--profiles", help="Comma-separated profile allowlist")
    parser.add_argument(
        "--include-nonlogged-profiles",
        action="store_true",
        help="With --profiles, include explicitly requested profiles even when temporarily marked for repair",
    )
    parser.add_argument("--no-restore", action="store_true", help="Do not restore account-02 after collection")
    return parser.parse_args()


def parse_int(value: str) -> int:
    cleaned = (value or "0").replace(",", "").strip()
    if cleaned in {"", "-"}:
        return 0
    return int(float(cleaned))


def parse_percent(value: str) -> float:
    cleaned = (value or "0").replace("%", "").strip()
    if cleaned in {"", "-"}:
        return 0.0
    return float(cleaned) / 100.0


def click_visible_text_wait(
    client: WebDriverClient,
    session_id: str,
    label: str,
    timeout_seconds: int = 30,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if client.execute(session_id, CLICK_VISIBLE_TEXT_JS, [label]):
            return True
        time.sleep(2)
    return False


def collect_pro_daily_reads(client: WebDriverClient, session_id: str) -> dict[str, Any]:
    client.request(f"/session/{session_id}/url", "POST", {"url": PRO_HOME_URL})
    time.sleep(5)
    current_url = str(client.request(f"/session/{session_id}/url"))
    if "/login" in current_url:
        raise RuntimeError("专业号平台登录状态已失效")

    for label, delay in (("数据中心", 2), ("笔记表现", 6), ("全部笔记", 6), ("近30日", 7)):
        if not click_visible_text_wait(client, session_id, label):
            raise RuntimeError(f"专业号页面未找到“{label}”")
        time.sleep(delay)

    period_text = client.execute(
        session_id,
        r"""
const match = document.body.innerText.match(/统计时间[：:]\s*(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})/);
return match ? {startDate: match[1], endDate: match[2]} : {};
""",
    )
    response = client.execute(session_id, READ_PRO_TREND_JS)
    if int(response.get("status") or 0) != 200:
        raise RuntimeError(f"专业号趋势接口返回 HTTP {response.get('status')}")
    payload = json.loads(response["text"])
    if not payload.get("success"):
        raise RuntimeError(f"专业号趋势接口失败: {payload.get('msg')}")
    trend = payload.get("data", {}).get("trend") or []
    daily = []
    for item in trend:
        note = item.get("note") or {}
        daily.append(
            {
                "date": item.get("dtm", ""),
                "reading_count": int(note.get("readNum") or 0),
            }
        )
    daily.sort(key=lambda row: row["date"])
    return {
        "period": {
            "start_date": period_text.get("startDate") or (daily[0]["date"] if daily else ""),
            "end_date": period_text.get("endDate") or (daily[-1]["date"] if daily else ""),
            "preset": "近30日",
        },
        "daily": daily,
        "total_reading_count": sum(row["reading_count"] for row in daily),
    }


def parse_creator_row(raw_row: dict[str, Any] | list[str]) -> dict[str, Any]:
    cells = raw_row.get("cells", []) if isinstance(raw_row, dict) else raw_row
    first_lines = [line.strip() for line in cells[0].splitlines() if line.strip()]
    published = next((line.removeprefix("发布于") for line in first_lines if line.startswith("发布于")), "")
    title = next((line for line in first_lines if not line.startswith("发布于")), "")
    return {
        "note_id": str(raw_row.get("noteId") or "").strip().lower() if isinstance(raw_row, dict) else "",
        "note_title": title,
        "published_at": published,
        "cumulative_exposures": parse_int(cells[1]),
        "cumulative_views": parse_int(cells[2]),
        "cover_click_rate": parse_percent(cells[3]),
    }


def collect_creator_notes(
    client: WebDriverClient,
    session_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    started_at = time.monotonic()
    print("  creator: opening data-analysis", flush=True)
    navigation_error: Exception | None = None
    try:
        client.request(f"/session/{session_id}/url", "POST", {"url": CREATOR_URL})
    except Exception as exc:
        navigation_error = exc
    time.sleep(7)
    current_url = str(client.request(f"/session/{session_id}/url"))
    if navigation_error and "/statistics/data-analysis" not in current_url:
        raise navigation_error
    if navigation_error:
        print(f"  creator: navigation response timed out but page loaded at {current_url}", file=sys.stderr, flush=True)
    if "/login" in current_url:
        raise RuntimeError("创作服务平台登录状态已失效")
    client.execute(session_id, INSTALL_CREATOR_RESPONSE_CAPTURE_JS)

    applied = client.execute(session_id, SET_CREATOR_DATE_RANGE_JS, [start_date, end_date])
    if not applied.get("ok"):
        if not click_visible_text_wait(client, session_id, "自定义", 20):
            diagnostic = client.execute(
                session_id,
                "return {url: location.href, title: document.title, text: document.body.innerText.slice(0, 500)};",
            )
            raise RuntimeError(f"创作服务平台未找到可用日期控件: {diagnostic}")
        time.sleep(2)
        applied = client.execute(session_id, SET_CREATOR_DATE_RANGE_JS, [start_date, end_date])
    if not applied.get("ok"):
        raise RuntimeError(f"创作服务平台日期控件不可用: {applied}")
    print(f"  creator: date range applied {applied.get('values')}", flush=True)
    time.sleep(8)

    notes: list[dict[str, Any]] = []
    note_ids: dict[tuple[str, str], str] = {}
    page = 1
    while True:
        if time.monotonic() - started_at > 600:
            raise RuntimeError("创作服务平台分页采集超过 10 分钟，已安全停止")
        note_ids.update(read_creator_note_ids(client, session_id))
        raw_rows = client.execute(session_id, READ_CREATOR_ROWS_JS)
        parsed_rows = [parse_creator_row(row) for row in raw_rows]
        for row in parsed_rows:
            row["note_id"] = row.get("note_id") or note_ids.get((row["note_title"], row["published_at"]), "")
        notes.extend(parsed_rows)
        visible_pages = client.execute(session_id, READ_CREATOR_PAGES_JS)
        print(f"  creator: page={page} rows={len(raw_rows)} visible_pages={visible_pages}", flush=True)
        next_page = page + 1
        if next_page not in visible_pages:
            break
        if not client.execute(session_id, CLICK_CREATOR_PAGE_JS, [next_page]):
            raise RuntimeError(f"创作服务平台无法切换到第 {next_page} 页")
        page = next_page
        time.sleep(4)
        if page > 50:
            raise RuntimeError("创作服务平台分页超过安全上限 50 页")

    unique = {(row.get("note_id") or row["note_title"], row["published_at"]): row for row in notes}
    ordered = sorted(unique.values(), key=lambda row: row["published_at"], reverse=True)
    return {
        "period": {"start_date": start_date, "end_date": end_date, "basis": "笔记发布时间"},
        "note_count": len(ordered),
        "note_id_count": sum(bool(row.get("note_id")) for row in ordered),
        "notes": ordered,
        "totals": {
            "cumulative_exposures": sum(row["cumulative_exposures"] for row in ordered),
            "cumulative_views": sum(row["cumulative_views"] for row in ordered),
            "weighted_cover_click_rate": (
                sum(row["cumulative_exposures"] * row["cover_click_rate"] for row in ordered)
                / sum(row["cumulative_exposures"] for row in ordered)
                if sum(row["cumulative_exposures"] for row in ordered)
                else 0.0
            ),
        },
    }


def collect_account(account: dict[str, str], start_date: str, end_date: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "profile": account["profile"],
        "email": account["email"],
        "configured_account_name": account["account_name"],
        "status": "error",
        "error": "",
    }
    try:
        run_accountctl(account["profile"])
        client = WebDriverClient(int(account["local_webdriver_port"]), timeout=45)
        client.wait_ready()
        session_id = create_content_session(client)
        try:
            result["account_name"] = account["account_name"]
            result["xiaohongshu_id"] = account.get("xiaohongshu_id", "")

            errors: list[str] = []
            try:
                result["creator_content_analysis"] = collect_creator_notes(
                    client, session_id, start_date, end_date
                )
            except Exception as exc:
                errors.append(f"创作者内容分析: {exc}")
            try:
                result["professional_note_performance"] = collect_pro_daily_reads(client, session_id)
            except Exception as exc:
                errors.append(f"专业号笔记表现: {exc}")

            if len(errors) == 2:
                result.update(status="error", error="；".join(errors))
            elif errors:
                result.update(status="partial", error="；".join(errors))
            else:
                result.update(status="ok")
            return result
        finally:
            client.close_session(session_id)
            time.sleep(1)
    except Exception as exc:
        result["error"] = str(exc)
        return result


def csv_text(fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + buffer.getvalue()


def write_outputs(output_dir: Path, run_date: str, payload: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"content-stats-{run_date}.json"
    daily_path = output_dir / f"daily-reading-{run_date}.csv"
    notes_path = output_dir / f"note-cumulative-{run_date}.csv"
    atomic_write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    daily_rows = []
    note_rows = []
    for account in payload["accounts"]:
        common = {
            "profile": account["profile"],
            "email": account["email"],
            "account_name": account.get("account_name", account["configured_account_name"]),
            "xiaohongshu_id": account.get("xiaohongshu_id", ""),
        }
        pro = account.get("professional_note_performance") or {}
        for row in pro.get("daily", []):
            daily_rows.append({**common, **row, "status": account["status"], "error": account.get("error", "")})
        creator = account.get("creator_content_analysis") or {}
        for row in creator.get("notes", []):
            note_rows.append({**common, **row, "status": account["status"], "error": account.get("error", "")})

    daily_fields = [
        "profile", "email", "account_name", "xiaohongshu_id", "date",
        "reading_count", "status", "error",
    ]
    note_fields = [
        "profile", "email", "account_name", "xiaohongshu_id", "note_id", "note_title",
        "published_at", "cumulative_exposures", "cumulative_views",
        "cover_click_rate", "status", "error",
    ]
    atomic_write_text(daily_path, csv_text(daily_fields, daily_rows))
    atomic_write_text(notes_path, csv_text(note_fields, note_rows))

    for source, latest_name in (
        (json_path, "latest.json"),
        (daily_path, "daily-reading-latest.csv"),
        (notes_path, "note-cumulative-latest.csv"),
    ):
        shutil.copyfile(source, output_dir / latest_name)
    return {"json": json_path, "daily": daily_path, "notes": notes_path}


def main() -> int:
    args = parse_args()
    today = dt.datetime.now(TIME_ZONE).date()
    end_date = dt.date.fromisoformat(args.end_date) if args.end_date else today
    previous_month_last = end_date.replace(day=1) - dt.timedelta(days=1)
    default_start = previous_month_last.replace(day=1)
    start_date = dt.date.fromisoformat(args.start_date) if args.start_date else default_start
    if start_date > end_date:
        raise SystemExit("--start-date must not be after --end-date")

    allowlist = set(args.profiles.split(",")) if args.profiles else None
    if args.include_nonlogged_profiles:
        if not allowlist:
            raise SystemExit("--include-nonlogged-profiles requires --profiles")
        with ACCOUNT_MAP.open("r", encoding="utf-8-sig", newline="") as handle:
            accounts = [row for row in csv.DictReader(handle, delimiter="\t") if row["profile"] in allowlist]
    else:
        accounts = read_accounts(allowlist)
    if not accounts:
        raise SystemExit(f"No logged-in accounts were found in {ACCOUNT_MAP}")

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another content collector run is already active", file=sys.stderr)
            return 3

        results = []
        try:
            for index, account in enumerate(accounts, start=1):
                print(f"[{index}/{len(accounts)}] collecting content {account['profile']} {account['email']}", flush=True)
                result = collect_account(account, start_date.isoformat(), end_date.isoformat())
                results.append(result)
                print(
                    json.dumps(
                        {
                            "profile": result["profile"],
                            "status": result["status"],
                            "daily_days": len((result.get("professional_note_performance") or {}).get("daily", [])),
                            "note_count": (result.get("creator_content_analysis") or {}).get("note_count", 0),
                            "note_id_count": (result.get("creator_content_analysis") or {}).get("note_id_count", 0),
                            "error": result.get("error", ""),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if index < len(accounts):
                    time.sleep(random.uniform(3.0, 7.0))
        finally:
            if not args.no_restore:
                try:
                    run_accountctl("account-02")
                except Exception as exc:
                    print(f"Warning: failed to restore account-02: {exc}", file=sys.stderr)

        payload = {
            "generated_at": dt.datetime.now(TIME_ZONE).isoformat(timespec="seconds"),
            "creator_note_period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            "professional_note_period": {"preset": "近30日", "note": "以平台最后完整数据日为截止日"},
            "successful_accounts": sum(item["status"] == "ok" for item in results),
            "partial_accounts": sum(item["status"] == "partial" for item in results),
            "failed_accounts": sum(item["status"] == "error" for item in results),
            "accounts": results,
        }
        paths = write_outputs(args.output_dir, end_date.isoformat(), payload)
        for kind, path in paths.items():
            print(f"{kind.upper()}: {path}")
        return 0 if all(item["status"] == "ok" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
