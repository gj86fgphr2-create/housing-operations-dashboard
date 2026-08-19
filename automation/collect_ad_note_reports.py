#!/usr/bin/env python3
"""Collect yesterday's Xiaohongshu Aurora note-ad report for every profile.

The collector reuses the existing isolated Selenium/Chromium profiles.  It
does not bypass login or verification.  Each profile is recorded separately
so that one expired login does not prevent the remaining profiles from being
processed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import random
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux production dependency
    fcntl = None

from collect_lead_stats import (
    PROJECT_DIR,
    TIME_ZONE,
    WebDriverClient,
    atomic_write_text,
    read_accounts,
    run_accountctl,
)


REPORT_URL = "https://ad.xiaohongshu.com/aurora/ad/datareports-basic/note"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "data" / "ad-note-stats"
LOCK_PATH = PROJECT_DIR / "data" / "ad-note-stats.lock"
REQUIRED_ROW_FIELDS = ("noteId", "fee", "initiativeMessage", "msgLeadsNum")
REPORT_API_PATH = "/api/leona/rtb/common/data/report"


INSTALL_CAPTURE_JS = r"""
if (!window.__xhsAdReportCaptureInstalled) {
  window.__xhsAdReportCaptureInstalled = true;
  window.__xhsAdReports = [];
  const keep = (entry) => {
    window.__xhsAdReports.push(entry);
    if (window.__xhsAdReports.length > 100) window.__xhsAdReports.shift();
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__xhsMethod = method;
    this.__xhsUrl = String(url || '');
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body) {
    this.__xhsBody = typeof body === 'string' ? body : '';
    this.addEventListener('load', function() {
      if (!this.__xhsUrl.includes('/api/leona/rtb/common/data/report')) return;
      let response = this.responseText;
      try { response = JSON.parse(response); } catch (_) {}
      keep({
        transport: 'xhr',
        url: this.__xhsUrl,
        method: this.__xhsMethod,
        requestBody: this.__xhsBody,
        status: this.status,
        response,
        capturedAt: Date.now(),
      });
    });
    return originalSend.apply(this, arguments);
  };

  const originalFetch = window.fetch;
  window.fetch = async function(input, init) {
    const response = await originalFetch.apply(this, arguments);
    const url = typeof input === 'string' ? input : String(input && input.url || '');
    if (url.includes('/api/leona/rtb/common/data/report')) {
      try {
        const clone = response.clone();
        const text = await clone.text();
        let payload = text;
        try { payload = JSON.parse(text); } catch (_) {}
        keep({
          transport: 'fetch',
          url,
          method: String(init && init.method || 'GET'),
          requestBody: typeof (init && init.body) === 'string' ? init.body : '',
          status: response.status,
          response: payload,
          capturedAt: Date.now(),
        });
      } catch (_) {}
    }
    return response;
  };
}
return {installed: true, captured: (window.__xhsAdReports || []).length};
"""


READ_PAGE_STATE_JS = r"""
const visible = (el) => !!el && el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden';
return {
  url: location.href,
  title: document.title,
  text: (document.body.innerText || '').slice(0, 5000),
  dates: Array.from(document.querySelectorAll('input'))
    .filter(visible)
    .map((el) => ({value: el.value || '', placeholder: el.placeholder || ''}))
    .filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.value) || /时间|日期/.test(row.placeholder)),
};
"""


CLICK_REPORT_MODE_JS = r"""
const wanted = arguments[0];
const visible = (el) => !!el && el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden';
const dateInput = Array.from(document.querySelectorAll('input')).find((el) =>
  visible(el) && (/^\d{4}-\d{2}-\d{2}$/.test(el.value || '') || /时间|日期/.test(el.placeholder || ''))
);
const dateRect = dateInput ? dateInput.getBoundingClientRect() : null;
const raw = Array.from(document.querySelectorAll('[role="option"],button,[role="button"],li,div,span'))
  .filter(visible)
  .filter((el) => (el.textContent || '').trim() === wanted);
const clickable = raw.map((el) => el.closest('[role="option"],button,[role="button"],li,.d-select-option') || el);
const unique = Array.from(new Set(clickable));
unique.sort((a, b) => {
  if (!dateRect) return 0;
  const ar = a.getBoundingClientRect();
  const br = b.getBoundingClientRect();
  const ad = Math.abs(ar.top - dateRect.top) + Math.abs(ar.left - dateRect.left);
  const bd = Math.abs(br.top - dateRect.top) + Math.abs(br.left - dateRect.left);
  return ad - bd;
});
const target = unique[0];
if (!target) return {ok: false, candidates: 0};
target.click();
return {ok: true, candidates: unique.length, tag: target.tagName, className: target.className || ''};
"""


SET_DATE_RANGE_JS = r"""
const wantedStart = arguments[0];
const wantedEnd = arguments[1];
const visible = (el) => !!el && el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden';
const candidates = Array.from(document.querySelectorAll('input'))
  .filter(visible)
  .filter((el) =>
    /^\d{4}-\d{2}-\d{2}$/.test(el.value || '') ||
    /开始|结束|时间|日期/.test(el.placeholder || '')
  );
if (candidates.length < 2) {
  return {ok: false, found: candidates.map((el) => ({value: el.value, placeholder: el.placeholder}))};
}
const inputs = candidates.slice(0, 2);
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
for (const [index, value] of [wantedStart, wantedEnd].entries()) {
  inputs[index].removeAttribute('readonly');
  setter.call(inputs[index], value);
  inputs[index].dispatchEvent(new Event('input', {bubbles: true}));
  inputs[index].dispatchEvent(new Event('change', {bubbles: true}));
  inputs[index].dispatchEvent(new Event('blur', {bubbles: true}));
}
document.body.click();
return {ok: true, values: inputs.map((el) => el.value)};
"""


CLICK_QUERY_JS = r"""
const labels = ['查询', '确定'];
const visible = (el) => !!el && el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden';
for (const label of labels) {
  const target = Array.from(document.querySelectorAll('button,[role="button"]'))
    .find((el) => visible(el) && (el.textContent || '').trim() === label);
  if (target) { target.click(); return {ok: true, label}; }
}
return {ok: false};
"""


READ_CAPTURED_JS = r"""
return (window.__xhsAdReports || []).map((entry) => ({
  transport: entry.transport,
  url: entry.url,
  method: entry.method,
  requestBody: entry.requestBody,
  status: entry.status,
  response: entry.response,
  capturedAt: entry.capturedAt,
}));
"""


READ_VISIBLE_PAGES_JS = r"""
return Array.from(document.querySelectorAll('.d-pagination-page-content'))
  .filter((el) => el.offsetParent !== null)
  .map((el) => Number.parseInt((el.textContent || '').trim(), 10))
  .filter(Number.isFinite);
"""


CLICK_PAGE_JS = r"""
const wanted = String(arguments[0]);
const target = Array.from(document.querySelectorAll('.d-pagination-page-content'))
  .find((el) => el.offsetParent !== null && (el.textContent || '').trim() === wanted);
if (!target) return false;
target.click();
return true;
"""


REQUEST_REPORT_PAGE_JS = r"""
const template = arguments[0];
const pageNum = Number(arguments[1]);
const startDate = arguments[2];
const endDate = arguments[3];
let body = {};
try { body = JSON.parse(template.requestBody || '{}'); } catch (_) { return {ok:false,error:'invalid request body'}; }
body.pageNum = pageNum;
body.startDate = startDate;
body.endDate = endDate;
body.timeUnit = 'DAY';
const xhr = new XMLHttpRequest();
try {
  xhr.open(template.method || 'POST', template.url, false);
  xhr.withCredentials = true;
  xhr.setRequestHeader('Content-Type', 'application/json;charset=UTF-8');
  xhr.send(JSON.stringify(body));
  let response = xhr.responseText;
  try { response = JSON.parse(response); } catch (_) {}
  return {ok:xhr.status===200,status:xhr.status,response,requestBody:JSON.stringify(body),url:template.url,method:template.method || 'POST'};
} catch (error) {
  return {ok:false,status:xhr.status || 0,error:String(error),requestBody:JSON.stringify(body),url:template.url,method:template.method || 'POST'};
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Report date in YYYY-MM-DD; default: yesterday")
    parser.add_argument("--start-date", help="Range start in YYYY-MM-DD")
    parser.add_argument("--end-date", help="Range end in YYYY-MM-DD")
    parser.add_argument("--profiles", help="Comma-separated profile allowlist")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-restore", action="store_true", help="Do not restore account-02")
    parser.add_argument("--page-timeout", type=int, default=45)
    return parser.parse_args()


def decode_json_strings(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in {"{", "["}:
            try:
                return decode_json_strings(json.loads(stripped))
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, list):
        return [decode_json_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: decode_json_strings(item) for key, item in value.items()}
    return value


def walk_dicts(value: Any):
    value = decode_json_strings(value)
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def parse_int(value: Any) -> int:
    if value is None or value is False:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", ".", "-."}:
        return 0
    return int(Decimal(cleaned))


def parse_money(value: Any) -> str:
    if value is None:
        return "0"
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", ".", "-."}:
        return "0"
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return "0"
    normalized = format(number, "f")
    compact = normalized.rstrip("0").rstrip(".") if "." in normalized else normalized
    return "0" if compact in {"", "-0"} else compact


def row_date(row: dict[str, Any], fallback: str) -> str:
    for key in ("date", "time", "statDate", "dataDate", "day", "dtm"):
        value = str(row.get(key) or "").strip()
        match = re.search(r"\d{4}-\d{2}-\d{2}", value)
        if match:
            return match.group(0)
    return fallback


def extract_note_rows(payload: Any, report_date: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in walk_dicts(payload):
        nested_metrics = decode_json_strings(item.get("dataValueJson"))
        metrics = nested_metrics if isinstance(nested_metrics, dict) else item
        note_id = str(item.get("noteId") or metrics.get("noteId") or "").strip()
        if not note_id:
            continue
        if not any(field in metrics for field in REQUIRED_ROW_FIELDS[1:]):
            continue
        candidates.append(
            {
                "date": row_date(item, report_date),
                "note_id": note_id,
                "spend": parse_money(metrics.get("fee")),
                "private_message_opens": parse_int(metrics.get("initiativeMessage")),
                "private_message_leads": parse_int(metrics.get("msgLeadsNum")),
                "owner_account_name": str(item.get("noteAuthor") or "").strip(),
                "owner_user_id": str(item.get("noteUserId") or "").strip(),
                "note_title": str(item.get("noteTitle") or "").strip(),
                "owner_source": "aurora_response" if item.get("noteAuthor") else "",
                "_note_jump_url": str(item.get("noteJumpUrl") or "").strip(),
            }
        )
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        key = (row["date"], row["note_id"])
        existing = deduplicated.get(key)
        score = sum(bool(row.get(field)) for field in ("owner_account_name", "owner_user_id", "note_title", "_note_jump_url"))
        existing_score = sum(bool(existing and existing.get(field)) for field in ("owner_account_name", "owner_user_id", "note_title", "_note_jump_url"))
        if existing is None or score > existing_score:
            deduplicated[key] = row
    return sorted(deduplicated.values(), key=lambda row: (row["date"], row["note_id"]))


READ_PUBLIC_NOTE_OWNER_JS = r"""
const links = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'))
  .filter((el) => el.offsetParent !== null);
const author = links.find((el) => (el.innerText || el.textContent || '').trim()) || links[0];
const href = author ? author.href : '';
const match = href.match(/\/user\/profile\/([^?/#]+)/);
const title = (document.title || '').replace(/\s*-\s*小红书\s*$/, '').trim();
return {
  url: location.href,
  ownerAccountName: author ? (author.innerText || author.textContent || '').trim() : '',
  ownerUserId: match ? match[1] : '',
  noteTitle: title,
  unavailable: /\/404(?:\?|$)/.test(location.pathname) || /Page Isn't Available|页面不见了/.test(document.body?.innerText || ''),
};
"""


def fill_missing_owners(
    client: WebDriverClient,
    session_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for row in rows:
        if row.get("owner_account_name") and row.get("owner_user_id"):
            continue
        jump_url = str(row.get("_note_jump_url") or "")
        if not jump_url:
            continue
        for attempt in range(2):
            try:
                client.request(f"/session/{session_id}/url", "POST", {"url": jump_url})
            except Exception:
                pass
            time.sleep(10 if attempt == 0 else 6)
            try:
                owner = client.execute(session_id, READ_PUBLIC_NOTE_OWNER_JS) or {}
            except Exception:
                owner = {}
            if owner.get("unavailable"):
                break
            owner_name = str(owner.get("ownerAccountName") or "").strip()
            owner_user_id = str(owner.get("ownerUserId") or "").strip()
            if owner_name and owner_user_id:
                row["owner_account_name"] = owner_name
                row["owner_user_id"] = owner_user_id
                row["owner_source"] = "official_note_jump"
                if not row.get("note_title"):
                    row["note_title"] = str(owner.get("noteTitle") or "").strip()
                break
    return rows


def capture_is_daily(entry: dict[str, Any], start_date: str, end_date: str | None = None) -> bool:
    body = str(entry.get("requestBody") or "")
    end_date = end_date or start_date
    if start_date not in body or end_date not in body:
        return False
    body_dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", body))
    if start_date == end_date and body_dates != {start_date}:
        return False
    upper = body.upper()
    return "DAY" in upper or "分日" in body


def latest_note_capture(captures: list[dict[str, Any]], start_date: str, end_date: str | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    end_date = end_date or start_date
    for entry in reversed(captures):
        if int(entry.get("status") or 0) != 200:
            continue
        rows = extract_note_rows(entry.get("response"), start_date)
        if rows and capture_is_daily(entry, start_date, end_date):
            return entry, rows
    if start_date != end_date:
        return None, []
    for entry in reversed(captures):
        if int(entry.get("status") or 0) != 200:
            continue
        rows = extract_note_rows(entry.get("response"), start_date)
        if rows:
            return entry, rows
    return None, []


def valid_daily_capture(entry: dict[str, Any], start_date: str, end_date: str | None = None) -> bool:
    if int(entry.get("status") or 0) != 200 or not capture_is_daily(entry, start_date, end_date):
        return False
    payload = decode_json_strings(entry.get("response"))
    for item in walk_dicts(payload):
        if "dataList" in item and isinstance(item.get("dataList"), list):
            return True
    return False


def create_performance_session(client: WebDriverClient) -> str:
    created = client.request(
        "/session",
        "POST",
        {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "chrome",
                    "goog:chromeOptions": {
                        "args": [
                            "--user-data-dir=/home/seluser/.config/chromium",
                            "--no-first-run",
                            "--no-default-browser-check",
                            "--disable-notifications",
                        ]
                    },
                    "goog:loggingPrefs": {"performance": "ALL"},
                }
            }
        },
    )
    return str(created["sessionId"])


def read_performance_captures(
    client: WebDriverClient, session_id: str
) -> list[dict[str, Any]]:
    logs = client.request(
        f"/session/{session_id}/se/log", "POST", {"type": "performance"}
    )
    requests: dict[str, dict[str, Any]] = {}
    captures: list[dict[str, Any]] = []
    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        method = message.get("method")
        params = message.get("params", {})
        request_id = str(params.get("requestId") or "")
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            url = str(request.get("url") or "")
            if REPORT_API_PATH in url:
                requests[request_id] = {
                    "transport": "performance",
                    "url": url,
                    "method": request.get("method", ""),
                    "requestBody": request.get("postData", ""),
                }
        elif method == "Network.responseReceived" and request_id in requests:
            response = params.get("response", {})
            capture = {**requests[request_id], "status": response.get("status", 0)}
            try:
                body = client.request(
                    f"/session/{session_id}/goog/cdp/execute",
                    "POST",
                    {
                        "cmd": "Network.getResponseBody",
                        "params": {"requestId": request_id},
                    },
                ).get("body", "")
                try:
                    capture["response"] = json.loads(body)
                except json.JSONDecodeError:
                    capture["response"] = body
            except Exception as exc:
                capture["response_error"] = str(exc)
            captures.append(capture)
    return captures


def wait_for_capture(
    client: WebDriverClient,
    session_id: str,
    start_date: str,
    end_date: str,
    previous_count: int,
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    captures: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        captures = client.execute(session_id, READ_CAPTURED_JS) or []
        if len(captures) > previous_count:
            recent = captures[previous_count:]
            _, rows = latest_note_capture(recent, start_date, end_date)
            if rows:
                return captures, rows
            if any(valid_daily_capture(entry, start_date, end_date) for entry in recent):
                return captures, []
        time.sleep(2)
    return captures, []


def configure_daily_range(
    client: WebDriverClient,
    session_id: str,
    start_date: str,
    end_date: str,
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    client.execute(session_id, INSTALL_CAPTURE_JS)
    captures_before = client.execute(session_id, READ_CAPTURED_JS) or []

    mode_opened = client.execute(session_id, CLICK_REPORT_MODE_JS, ["汇总"])
    if mode_opened.get("ok"):
        time.sleep(1)
    mode_selected = client.execute(session_id, CLICK_REPORT_MODE_JS, ["分日"])
    if not mode_selected.get("ok"):
        raise RuntimeError(f"未找到分日选项: {mode_selected}")
    time.sleep(2)

    applied = client.execute(session_id, SET_DATE_RANGE_JS, [start_date, end_date])
    if not applied.get("ok") or applied.get("values") != [start_date, end_date]:
        raise RuntimeError(f"日期范围未正确应用: {applied}")

    captures, rows = wait_for_capture(
        client, session_id, start_date, end_date, len(captures_before), min(timeout_seconds, 8)
    )
    if rows:
        return captures, rows

    client.execute(session_id, CLICK_QUERY_JS)
    return wait_for_capture(
        client, session_id, start_date, end_date, len(captures), timeout_seconds
    )


def request_report_pages(
    client: WebDriverClient,
    session_id: str,
    template: dict[str, Any],
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    page_entries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    total_count: int | None = None
    for page in range(1, 101):
        entry = client.execute(
            session_id,
            REQUEST_REPORT_PAGE_JS,
            [template, page, start_date, end_date],
        ) or {}
        if not entry.get("ok"):
            raise RuntimeError(f"第 {page} 页接口请求失败: {entry.get('status')} {entry.get('error','')}")
        page_entries.append(entry)
        if total_count is None:
            for item in walk_dicts(entry.get("response")):
                if "totalCount" in item and "pageSize" in item:
                    total_count = parse_int(item.get("totalCount"))
                    break
        page_rows = extract_note_rows(entry.get("response"), start_date)
        if not page_rows:
            break
        all_rows.extend(page_rows)
        if total_count is not None and page * 20 >= total_count:
            break
        if total_count is None and len(page_rows) < 20:
            break
    return page_entries, all_rows


def collect_account(
    account: dict[str, str],
    start_date: str,
    page_timeout: int,
    end_date: str | None = None,
) -> dict[str, Any]:
    end_date = end_date or start_date
    result: dict[str, Any] = {
        "profile": account["profile"],
        "account_name": account["account_name"],
        "xiaohongshu_id": account.get("xiaohongshu_id", ""),
        "date": end_date,
        "start_date": start_date,
        "end_date": end_date,
        "status": "error",
        "error": "",
        "rows": [],
    }
    try:
        run_accountctl(account["profile"])
        client = WebDriverClient(int(account["local_webdriver_port"]), timeout=45)
        client.wait_ready()
        session_id = create_performance_session(client)
        try:
            initial_capture_count = 0
            client.request(f"/session/{session_id}/url", "POST", {"url": REPORT_URL})
            time.sleep(10)
            state = client.execute(session_id, READ_PAGE_STATE_JS)
            current_url = str(state.get("url") or "")
            page_text = str(state.get("text") or "")
            if current_url.rstrip("/") == "https://ad.xiaohongshu.com" or "短信登录" in page_text:
                result.update(status="not_logged_in", error="聚光平台登录状态不存在或已失效")
                return result
            if "/datareports-basic/note" not in current_url:
                raise RuntimeError(f"未进入标准投笔记报表: {current_url}")

            initial_captures = read_performance_captures(client, session_id)
            valid_initial = [
                entry for entry in initial_captures
                if valid_daily_capture(entry, start_date, end_date)
            ]
            if valid_initial:
                initial_capture_count = len(initial_captures)
                rows = extract_note_rows(valid_initial[-1].get("response"), start_date)
                client.execute(session_id, INSTALL_CAPTURE_JS)
                captures = valid_initial
            else:
                captures, rows = configure_daily_range(
                    client, session_id, start_date, end_date, page_timeout
                )
                valid_configured = [
                    entry for entry in captures
                    if valid_daily_capture(entry, start_date, end_date)
                ]
                if not rows and not valid_configured:
                    raise RuntimeError("未捕获到已验证的分日笔记报表响应")
            template_capture = next(
                (entry for entry in reversed(captures) if capture_is_daily(entry, start_date, end_date)),
                None,
            )
            if template_capture:
                direct_entries, all_rows = request_report_pages(
                    client, session_id, template_capture, start_date, end_date
                )
                captures.extend(direct_entries)
            else:
                all_rows = list(rows)

            unique_rows = {
                (row["date"], row["note_id"]): row
                for row in all_rows
            }
            ordered_rows = sorted(
                unique_rows.values(), key=lambda row: (row["date"], row["note_id"])
            )
            wrong_dates = sorted({row["date"] for row in ordered_rows if not start_date <= row["date"] <= end_date})
            if wrong_dates:
                raise RuntimeError(f"报表返回了非目标日期: {wrong_dates}")
            fill_missing_owners(client, session_id, ordered_rows)
            unresolved = [
                row["note_id"] for row in ordered_rows
                if not row.get("owner_account_name") or not row.get("owner_user_id")
            ]
            for row in ordered_rows:
                row["owner_status"] = "confirmed" if row["note_id"] not in unresolved else "unresolved"
                row.pop("_note_jump_url", None)
            result.update(
                status="ok" if not unresolved else "error",
                error="" if not unresolved else f"存在 {len(unresolved)} 个未确认归属的笔记ID",
                row_count=len(ordered_rows),
                rows=ordered_rows,
                captured_requests=initial_capture_count + len(captures),
                unresolved_owner_count=len(unresolved),
                unresolved_note_ids=unresolved,
            )
            if os.environ.get("XHS_AD_DEBUG_CAPTURE") == "1":
                result["debug_captures"] = captures
            return result
        finally:
            client.close_session(session_id)
            time.sleep(1)
    except Exception as exc:
        result["error"] = str(exc)
        return result


def csv_text(rows: list[dict[str, Any]]) -> str:
    fields = [
        "date",
        "profile",
        "account_name",
        "xiaohongshu_id",
        "note_id",
        "spend",
        "private_message_opens",
        "private_message_leads",
        "owner_account_name",
        "owner_user_id",
        "note_title",
        "owner_source",
        "owner_status",
        "status",
        "error",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + buffer.getvalue()


def write_outputs(output_dir: Path, start_date: str, end_date: str, payload: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    period_label = start_date if start_date == end_date else f"{start_date}-to-{end_date}"
    json_path = output_dir / f"ad-note-stats-{period_label}.json"
    csv_path = output_dir / f"ad-note-stats-{period_label}.csv"
    atomic_write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    flat_rows: list[dict[str, Any]] = []
    for account in payload["accounts"]:
        common = {
            "date": end_date,
            "profile": account["profile"],
            "account_name": account["account_name"],
            "xiaohongshu_id": account.get("xiaohongshu_id", ""),
            "status": account["status"],
            "error": account.get("error", ""),
        }
        if account.get("rows"):
            flat_rows.extend({**common, **row} for row in account["rows"])
        else:
            flat_rows.append(
                {
                    **common,
                    "note_id": "",
                    "spend": "0",
                    "private_message_opens": 0,
                    "private_message_leads": 0,
                    "owner_account_name": "",
                    "owner_user_id": "",
                    "note_title": "",
                    "owner_source": "",
                    "owner_status": "not_applicable",
                }
            )
    rendered_csv = csv_text(flat_rows)
    atomic_write_text(csv_path, rendered_csv)
    atomic_write_text(output_dir / "latest.json", json_path.read_text(encoding="utf-8"))
    atomic_write_text(output_dir / "latest.csv", rendered_csv)
    return {"json": json_path, "csv": csv_path}


def main() -> int:
    args = parse_args()
    if args.date and (args.start_date or args.end_date):
        raise SystemExit("Use either --date or --start-date/--end-date")
    if bool(args.start_date) != bool(args.end_date):
        raise SystemExit("--start-date and --end-date must be used together")
    if args.start_date:
        start_date = dt.date.fromisoformat(args.start_date)
        end_date = dt.date.fromisoformat(args.end_date)
    else:
        start_date = end_date = (
            dt.date.fromisoformat(args.date)
            if args.date
            else dt.datetime.now(TIME_ZONE).date() - dt.timedelta(days=1)
        )
    today = dt.datetime.now(TIME_ZONE).date()
    if start_date > end_date or end_date >= today:
        raise SystemExit("Date range must be ordered and earlier than today")
    allowlist = set(args.profiles.split(",")) if args.profiles else None
    accounts = read_accounts(allowlist)
    if not accounts:
        raise SystemExit("No configured logged-in profiles were found")

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_handle:
        if fcntl is not None:
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("Another ad-note collector run is already active", file=sys.stderr)
                return 3

        results = []
        try:
            for index, account in enumerate(accounts, start=1):
                print(
                    f"[{index}/{len(accounts)}] collecting Aurora notes {account['profile']}",
                    flush=True,
                )
                result = collect_account(
                    account, start_date.isoformat(), args.page_timeout, end_date.isoformat()
                )
                results.append(result)
                print(
                    json.dumps(
                        {
                            "profile": result["profile"],
                            "status": result["status"],
                            "row_count": len(result.get("rows") or []),
                            "error": result.get("error", ""),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if index < len(accounts):
                    time.sleep(random.uniform(2.0, 5.0))
        finally:
            if not args.no_restore:
                try:
                    run_accountctl("account-02")
                except Exception as exc:
                    print(f"Warning: failed to restore account-02: {exc}", file=sys.stderr)

        payload = {
            "generated_at": dt.datetime.now(TIME_ZONE).isoformat(timespec="seconds"),
            "date": end_date.isoformat(),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "source": REPORT_URL,
            "aggregation": "DAY",
            "successful_accounts": sum(item["status"] == "ok" for item in results),
            "not_logged_in_accounts": sum(item["status"] == "not_logged_in" for item in results),
            "failed_accounts": sum(item["status"] == "error" for item in results),
            "accounts": results,
        }
        paths = write_outputs(args.output_dir, start_date.isoformat(), end_date.isoformat(), payload)
        for kind, path in paths.items():
            print(f"{kind.upper()}: {path}")
        return 0 if all(item["status"] == "ok" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
