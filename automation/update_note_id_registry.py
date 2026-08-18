#!/usr/bin/env python3
"""Append newly collected Xiaohongshu note IDs to a three-column XLSX registry."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"x": MAIN_NS}
NOTE_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
SENTINELS = {"__ACCOUNT_ID__", "__ACCOUNT_NAME__", "__NOTE_ID__"}
SHEET_PATH = "xl/worksheets/sheet1.xml"

ET.register_namespace("x", MAIN_NS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Content collector latest.json")
    parser.add_argument("--template", type=Path, required=True, help="Three-column XLSX template")
    parser.add_argument("--output", type=Path, required=True, help="Persistent XLSX registry")
    parser.add_argument("--window-days", type=int, default=30, help="Only add notes published in this trailing window")
    return parser.parse_args()


def cell_text(cell: ET.Element) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS))
    value = cell.find("x:v", NS)
    return value.text if value is not None and value.text else ""


def read_registry_rows(sheet_xml: bytes) -> tuple[ET.Element, list[tuple[str, str, str]], str]:
    root = ET.fromstring(sheet_xml)
    rows: list[tuple[str, str, str]] = []
    body_style = "0"
    sheet_data = root.find("x:sheetData", NS)
    if sheet_data is None:
        raise RuntimeError("XLSX template is missing sheetData")
    for row in sheet_data.findall("x:row", NS):
        if row.get("r") == "1":
            continue
        cells = row.findall("x:c", NS)
        if cells and cells[0].get("s"):
            body_style = cells[0].get("s", body_style)
        values = [cell_text(cell).strip() for cell in cells]
        if len(values) >= 3 and not any(value in SENTINELS for value in values[:3]):
            rows.append((values[0], values[1], values[2]))
    return root, rows, body_style


def published_date(value: Any) -> dt.date | None:
    text = str(value or "").strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def collected_rows(payload: dict[str, Any], window_days: int) -> tuple[list[tuple[str, str, str]], dt.date]:
    end_text = str(payload.get("creator_note_period", {}).get("end_date") or "")
    end_date = dt.date.fromisoformat(end_text)
    cutoff = end_date - dt.timedelta(days=window_days - 1)
    rows: list[tuple[str, str, str]] = []
    for account in payload.get("accounts", []):
        account_id = str(account.get("xiaohongshu_id") or "").strip()
        account_name = str(account.get("account_name") or account.get("configured_account_name") or "").strip()
        notes = (account.get("creator_content_analysis") or {}).get("notes", [])
        for note in notes:
            note_id = str(note.get("note_id") or "").strip().lower()
            note_date = published_date(note.get("published_at"))
            if account_id and account_name and NOTE_ID_RE.fullmatch(note_id) and note_date and cutoff <= note_date <= end_date:
                rows.append((account_id, account_name, note_id))
    return rows, cutoff


def string_cell(row_number: int, column: str, value: str, style: str = "16") -> ET.Element:
    cell = ET.Element(f"{{{MAIN_NS}}}c", {"r": f"{column}{row_number}", "s": style, "t": "inlineStr"})
    inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
    text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
    text.text = value
    return cell


def rebuild_sheet(root: ET.Element, rows: list[tuple[str, str, str]], body_style: str) -> bytes:
    sheet_data = root.find("x:sheetData", NS)
    if sheet_data is None:
        raise RuntimeError("XLSX template is missing sheetData")
    header = next((row for row in sheet_data.findall("x:row", NS) if row.get("r") == "1"), None)
    if header is None:
        raise RuntimeError("XLSX template is missing its header row")
    sheet_data.clear()
    sheet_data.append(header)
    for row_number, values in enumerate(rows, start=2):
        row = ET.SubElement(sheet_data, f"{{{MAIN_NS}}}row", {"r": str(row_number), "ht": "22", "customHeight": "1"})
        for column, value in zip(("A", "B", "C"), values):
            row.append(string_cell(row_number, column, value, body_style))

    row_count = max(1, len(rows) + 1)
    dimension = root.find("x:dimension", NS)
    if dimension is None:
        dimension = ET.Element(f"{{{MAIN_NS}}}dimension")
        root.insert(0, dimension)
    dimension.set("ref", f"A1:C{row_count}")

    sheet_view = root.find("x:sheetViews/x:sheetView", NS)
    if sheet_view is not None and sheet_view.find("x:pane", NS) is None:
        ET.SubElement(sheet_view, f"{{{MAIN_NS}}}pane", {
            "ySplit": "1", "topLeftCell": "A2", "activePane": "bottomLeft", "state": "frozen",
        })

    auto_filter = root.find("x:autoFilter", NS)
    if auto_filter is None:
        auto_filter = ET.Element(f"{{{MAIN_NS}}}autoFilter")
        children = list(root)
        sheet_index = children.index(sheet_data)
        root.insert(sheet_index + 1, auto_filter)
    auto_filter.set("ref", f"A1:C{row_count}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_workbook(source: Path, output: Path, sheet_xml: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(source, "r") as source_zip, zipfile.ZipFile(temp_path, "w") as output_zip:
            for info in source_zip.infolist():
                data = sheet_xml if info.filename == SHEET_PATH else source_zip.read(info.filename)
                output_zip.writestr(info, data)
        temp_path.replace(output)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main() -> int:
    args = parse_args()
    if args.window_days < 1:
        raise SystemExit("--window-days must be positive")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    source = args.output if args.output.is_file() else args.template
    if not source.is_file():
        raise SystemExit(f"XLSX source is missing: {source}")
    with zipfile.ZipFile(source, "r") as workbook:
        root, existing, body_style = read_registry_rows(workbook.read(SHEET_PATH))

    incoming, cutoff = collected_rows(payload, args.window_days)
    current_names = {account_id: name for account_id, name, _ in incoming}
    merged: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for account_id, account_name, note_id in existing + incoming:
        key = (account_id, note_id.lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append((account_id, current_names.get(account_id, account_name), note_id.lower()))

    new_count = len(merged) - len({(account_id, note_id.lower()) for account_id, _, note_id in existing})
    sheet_xml = rebuild_sheet(root, merged, body_style)
    write_workbook(source, args.output, sheet_xml)
    print(json.dumps({
        "output": str(args.output),
        "windowDays": args.window_days,
        "cutoffDate": cutoff.isoformat(),
        "existingRows": len(existing),
        "incomingRows": len(incoming),
        "newRows": new_count,
        "totalRows": len(merged),
        "accountCount": len({row[0] for row in merged}),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
