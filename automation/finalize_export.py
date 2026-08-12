#!/usr/bin/env python3
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook


FILES = ("房源详情.xlsx", "在租中合同.xlsx", "将搬入合同.xlsx", "已退租合同.xlsx", "预定合同.xlsx")
DASHBOARD_URL = "https://gj86fgphr2-create.github.io/housing-operations-dashboard/#operations-brief"
EXPECTED_STATUS = {
    "在租中合同.xlsx": "在租中",
    "将搬入合同.xlsx": "将搬入",
    "已退租合同.xlsx": "已退租",
}


def text(value):
    return "" if value is None else str(value).strip()


def inspect_contract(path, expected):
    ws = load_workbook(path, read_only=True, data_only=True).active
    headers = [text(c.value) for c in ws[3]]
    status_col = headers.index("合同状态") + 1
    rows = 0
    invalid = 0
    for row in ws.iter_rows(min_row=4, values_only=True):
        if not any(text(v) for v in row):
            continue
        rows += 1
        if text(row[status_col - 1]) != expected:
            invalid += 1
    return rows, invalid == 0, f"状态均为“{expected}”" if invalid == 0 else f"发现{invalid}条状态异常"


def inspect_house(path):
    ws = load_workbook(path, read_only=True, data_only=True).active
    headers = [text(c.value) for c in ws[3]]
    id_col = headers.index("房源ID")
    estate_col = headers.index("小区/公寓")
    rows = 0
    excluded = 0
    for row in ws.iter_rows(min_row=4, values_only=True):
        if not any(text(v) for v in row):
            continue
        rows += 1
        if text(row[id_col]) == "117492563" or "物业租赁中心（路线指引）" in text(row[estate_col]):
            excluded += 1
    ok = rows > 0 and excluded == 0
    return rows, ok, "已删除指定排除项及空白尾行" if ok else f"发现{excluded}条应排除数据"


def inspect_reservation(path):
    ws = load_workbook(path, read_only=True, data_only=True).active
    headers = [text(c.value) for c in ws[1]]
    status_col = headers.index("状态")
    rows = 0
    invalid = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(text(v) for v in row):
            continue
        rows += 1
        if text(row[status_col]) != "已付定":
            invalid += 1
    return rows, invalid == 0, "仅包含“已付定”" if invalid == 0 else f"发现{invalid}条非已付定数据"


def dashboard_brief(path):
    html = path.read_text(encoding="utf-8")
    match = re.search(r"const DATA\s*=\s*(\{.*?\});\s*\n\s*const \$", html, re.S)
    if not match:
        raise RuntimeError("工作台数据载荷不存在")
    data = json.loads(match.group(1))
    rows = data.get("contractStats", {}).get("recentPerformance", [])
    today = next((row for row in rows if row.get("date") == data.get("dataDate")), None)
    if today is None:
        raise RuntimeError("工作台缺少当天运营简报")
    return {
        "date": data["dataDate"],
        "generatedAt": data.get("generatedDate", ""),
        "newCount": int(today.get("newCount", 0) or 0),
        "renewalCount": int(today.get("renewalCount", 0) or 0),
        "actualCheckoutCount": int(today.get("actualCheckoutCount", 0) or 0),
    }


def main():
    run_dir = Path(sys.argv[1]).resolve()
    queue_dir = Path(sys.argv[2]).resolve()
    dashboard_path = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else Path("/opt/yuxiaor-automation/site/index.html")
    dashboard_url = sys.argv[4] if len(sys.argv) > 4 else DASHBOARD_URL
    queue_dir.mkdir(parents=True, exist_ok=True)

    checks = []
    for filename in FILES:
        path = run_dir / filename
        if not path.exists() or path.stat().st_size == 0:
            checks.append({"file": filename, "rows": 0, "ok": False, "detail": "文件缺失或为空"})
            continue
        try:
            if filename == "房源详情.xlsx":
                rows, ok, detail = inspect_house(path)
            elif filename == "预定合同.xlsx":
                rows, ok, detail = inspect_reservation(path)
            else:
                rows, ok, detail = inspect_contract(path, EXPECTED_STATUS[filename])
            checks.append({"file": filename, "rows": rows, "ok": ok, "detail": detail, "bytes": path.stat().st_size})
        except Exception as exc:
            checks.append({"file": filename, "rows": 0, "ok": False, "detail": f"无法读取：{exc}"})

    valid = all(item["ok"] for item in checks)
    completed = datetime.now().astimezone()
    validation = {
        "valid": valid,
        "runDirectory": str(run_dir),
        "completedAt": completed.isoformat(),
        "checks": checks,
    }
    (run_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    if not valid:
        raise SystemExit("Export validation failed: " + json.dumps(validation, ensure_ascii=False))

    try:
        brief = dashboard_brief(dashboard_path)
    except Exception as exc:
        raise SystemExit(f"Dashboard validation failed: {exc}")

    lines = [
        f"**习院数据每小时汇报**",
        f"> 导出时间：{completed.strftime('%Y-%m-%d %H:%M:%S')}",
        "> 校验结果：✅ 5份文件全部通过",
        "",
        f"**今日情况（{brief['date']}）**",
        f"> 新签 **{brief['newCount']}份** ｜ 续租 **{brief['renewalCount']}份** ｜ 实际退租 **{brief['actualCheckoutCount']}份**",
        "",
        f"[查看最新在线工作台]({dashboard_url})",
        "",
    ]
    for item in checks:
        label = item["file"].removesuffix(".xlsx")
        lines.append(f"- {label}：**{item['rows']}条**（{item['detail']}）")
    lines.extend(["", "文件将按顺序发送，请以本次时间戳识别。"])

    job = {
        "id": f"{completed.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "chatid": "wrki7WEAAAYzG-hYJ4delzv_Y7Us71ow",
        "summary": "\n".join(lines),
        "dashboard": {"url": dashboard_url, **brief},
        "files": [str(run_dir / filename) for filename in FILES],
        "validation": validation,
    }
    temp = queue_dir / f".{job['id']}.tmp"
    target = queue_dir / f"{job['id']}.json"
    temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    print(json.dumps(validation, ensure_ascii=False))


if __name__ == "__main__":
    main()

