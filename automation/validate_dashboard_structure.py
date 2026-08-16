#!/usr/bin/env python3
"""Reject dashboard output that lost the protected navigation or XHS account view."""

import json
import re
import sys
from pathlib import Path


REQUIRED = (
    'class="nav desktop-nav"',
    'data-desktop-module="xiaohongshu"',
    'data-desktop-module="yuxiaor"',
    'data-desktop-menu="xiaohongshu"',
    'data-desktop-menu="yuxiaor"',
    'class="mobile-nav-shell"',
    'id="xhs-content-updated"',
    'data-mobile-menu="primary"',
    'data-mobile-module="xiaohongshu"',
    'data-mobile-module="yuxiaor"',
    'data-mobile-menu="xiaohongshu"',
    'data-mobile-menu="yuxiaor"',
    'id="xhs-account"',
    'id="xhs-account-table"',
    'id="xhs-account-status-list"',
    'id="xhs-notes"',
    'id="xhs-note-count-head"',
    'id="xhs-note-count-table"',
    'id="xhs-view-count-head"',
    'id="xhs-view-count-table"',
    'id="xhs-exposure-count-head"',
    'id="xhs-exposure-count-table"',
    'id="xhs-daily-reading-chart"',
    'id="xhs-leads"',
    'id="xhs-leads-updated"',
    'id="xhs-lead-opened-table"',
    'id="xhs-lead-copied-table"',
    'xhs-goal-table',
    'function xhsGoalCell(',
    '"targetMonth"',
    '"targets"',
    'function renderXhsLeads()',
    'id="checkout-pressure"',
    'data-dashboard-view="occupancy"',
)

FORBIDDEN = (
    'data-dashboard-view="checkout"',
    'id="xhs-lead-inbound-table"',
)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} DASHBOARD_HTML", file=sys.stderr)
        return 2

    dashboard = Path(sys.argv[1])
    html = dashboard.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED if marker not in html]
    forbidden = [marker for marker in FORBIDDEN if marker in html]
    if missing or forbidden:
        if missing:
            print("missing protected markers: " + ", ".join(missing), file=sys.stderr)
        if forbidden:
            print("legacy markers detected: " + ", ".join(forbidden), file=sys.stderr)
        return 1

    payload_match = re.search(r"const DATA\s*=\s*(\{.*?\});", html, re.S)
    if not payload_match:
        print("dashboard DATA payload missing", file=sys.stderr)
        return 1
    payload = json.loads(payload_match.group(1))
    leads = payload.get("xhsLeads", {})
    accounts = leads.get("accounts", [])
    target_rows = [
        target
        for account in accounts
        for target in account.get("targets", {}).values()
    ]
    opened_total = sum(int(target.get("opened", 0)) for target in target_rows)
    copied_total = sum(int(target.get("copied", 0)) for target in target_rows)
    if leads.get("month") == "2026-08":
        if leads.get("targetMonth") != "2026-08" or len(accounts) != 8 or len(target_rows) != 40:
            print("XHS August target coverage invalid", file=sys.stderr)
            return 1
        if (opened_total, copied_total) != (2267, 1462):
            print(f"XHS August target totals invalid: {(opened_total, copied_total)}", file=sys.stderr)
            return 1

    print(f"dashboard structure valid: {dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
