#!/usr/bin/env python3
"""Reject dashboard output that lost the protected navigation or XHS account view."""

import sys
from pathlib import Path


REQUIRED = (
    'class="nav desktop-nav"',
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
    'id="xhs-lead-inbound-table"',
    'id="xhs-lead-opened-table"',
    'id="xhs-lead-copied-table"',
    'function renderXhsLeads()',
    'id="checkout-pressure"',
    'data-dashboard-view="occupancy"',
)

FORBIDDEN = (
    'data-dashboard-view="checkout"',
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

    print(f"dashboard structure valid: {dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
