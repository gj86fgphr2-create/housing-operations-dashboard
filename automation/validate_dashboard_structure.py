#!/usr/bin/env python3
"""Reject dashboard output that lost the protected navigation or XHS account view."""

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path


REQUIRED = (
    'class="nav desktop-nav"',
    'data-desktop-module="xiaohongshu"',
    'data-desktop-module="yuxiaor"',
    'data-desktop-menu="xiaohongshu"',
    'data-desktop-menu="yuxiaor"',
    'data-desktop-module="customer"',
    'data-desktop-menu="customer"',
    'class="desktop-home-link"',
    'class="desktop-nav-groups"',
    'class="desktop-nav-group"',
    'class="desktop-module-toggle"',
    'aria-expanded="false"',
    'aria-expanded="true"',
    'class="mobile-nav-shell"',
    'id="xhs-content-updated"',
    'data-mobile-menu="primary"',
    'data-mobile-module="xiaohongshu"',
    'data-mobile-module="yuxiaor"',
    'data-mobile-menu="xiaohongshu"',
    'data-mobile-menu="yuxiaor"',
    'data-mobile-module="customer"',
    'data-mobile-menu="customer"',
    'data-desktop-module="meters"',
    'data-desktop-menu="meters"',
    'data-mobile-module="meters"',
    'data-mobile-menu="meters"',
    'id="meter-management"',
    'data-dashboard-view="meter-management"',
    'id="meter-collection-status"',
    'id="meter-keep-table"',
    'id="meter-negative-table"',
    'id="meter-offline-table"',
    'function renderMeterManagement()',
    '"meterManagement"',
    '空房不可租按“预定 → 将搬入 → 锁房”互斥归类',
    '按预定、将搬入、锁房顺序互斥归类',
    '第一优先匹配已付定合同',
    '第二优先匹配将搬入合同',
    '扣除预定、将搬入后的剩余不可租',
    '仅从剩余锁房中按备注包含“短租”筛选',
    'd.validation?.shortRentWithinLocked',
    'data-dashboard-view="business-trend"',
    'id="business-trend"',
    'id="business-trend-chart"',
    'id="business-trend-summary"',
    'function renderBusinessTrend()',
    '"businessTrend"',
    '最新日期在左',
    '每日数值直接标注',
    'labelY=Math.max(18,pointY-labelOffset)',
    'id="contract-daily-trend-panel"',
    'class="panel contract-daily-trend-panel"',
    'id="contract-daily-chart-wrap"',
    'id="contract-daily-chart"',
    'function renderDailyLineChart()',
    'renderBusinessTrend(); renderDailyLineChart(); renderCheckoutTrends();',
    'id="checkout-trend-grid"',
    'id="checkout-trend-future-chart"',
    'id="checkout-trend-past-chart"',
    'id="checkout-trend-future-summary"',
    'id="checkout-trend-past-summary"',
    'function renderCheckoutTrends()',
    'function renderCheckoutTrendChart(',
    '"checkoutTrends"',
    '未来30天',
    '过去30天',
    'checkoutLabelY=Math.max(18,pointY-9)',
    'id="customer-data"',
    'data-dashboard-view="customer-data"',
    'id="customer-data-updated"',
    'id="customer-daily-table"',
    'id="customer-funnel-table"',
    'function renderCustomerData()',
    '"customerData"',
    'id="xhs-account"',
    'id="xhs-account-updated"',
    'id="xhs-account-table"',
    'id="xhs-account-status-list"',
    'adCollectedAt',
    'adCollectedOk',
    'leadCollectedOk',
    'noteCollectedOk',
    'function xhsCollectedBadge(',
    'class="xhs-collection-badge ok"',
    'leadCollectedAt',
    'noteCollectedAt',
    'function xhsCollectedHour(',
    '<th>聚光</th><th>留资</th><th>笔记</th>',
    'id="xhs-notes"',
    'id="xhs-note-count-head"',
    'id="xhs-note-count-table"',
    'id="xhs-view-count-head"',
    'id="xhs-view-count-table"',
    'function xhsMetricTotal(account,weeks,field)',
    'function xhsNoteCountClass(',
    'function xhsMetricCell(',
    'xhs-note-count-green',
    'xhs-note-count-yellow',
    'xhs-note-count-pink',
    'xhs-note-count-red',
    'if(count>=6)',
    'if(count===5)',
    'if(count===4)',
    '<th>汇总</th>',
    'id="xhs-daily-reading-chart"',
    'id="xhs-note-published"',
    'data-dashboard-view="xhs-note-published"',
    'id="xhs-note-published-updated"',
    'id="xhs-note-published-account"',
    'id="xhs-note-published-type"',
    'id="xhs-note-published-count"',
    'id="xhs-note-published-table"',
    'function renderXhsNotePublished()',
    '"xhsNotePublished"',
    '图文数量',
    '视频数量',
    '待识别数量',
    'graphicCount',
    'videoCount',
    'pendingCount',
    '数据明细',
    '笔记发布明细',
    '留资数据明细',
    '聚光投放明细',
    'data-mobile-menu="xhs-details"',
    'data-mobile-submenu="xhs-details"',
    'id="xhs-ad-details"',
    'data-dashboard-view="xhs-ad-details"',
    'id="xhs-ad-details-updated"',
    'id="xhs-ad-detail-account-filter"',
    'id="xhs-ad-detail-start-date"',
    'id="xhs-ad-detail-end-date"',
    'id="xhs-ad-detail-date-reset"',
    'id="xhs-ad-details-count"',
    'id="xhs-ad-details-table"',
    'function renderXhsAdDetails(',
    'id="xhs-leads"',
    'id="xhs-leads-updated"',
    'id="xhs-lead-opened-table"',
    'id="xhs-lead-copied-table"',
    'id="xhs-lead-details"',
    'data-dashboard-view="xhs-lead-details"',
    'id="xhs-lead-detail-account"',
    'id="xhs-lead-detail-table"',
    'id="xhs-ad-flow"',
    'data-dashboard-view="xhs-ad-flow"',
    'id="xhs-ad-account-table"',
    'id="xhs-ad-note-table"',
    'id="xhs-ad-start-date"',
    'id="xhs-ad-end-date"',
    'function xhsAdSummarizeAccountRows(',
    'function xhsAdSummarizeOwnerRows(',
    '汇总为每个投流账号一行',
    '汇总为每个归属账号一行',
    'function xhsAdPrepareDateControls(',
    'id="xhs-ad-team-filter"',
    'id="xhs-ad-account-filter"',
    'id="xhs-ad-spend-chart-wrap"',
    'id="xhs-ad-spend-chart"',
    'id="xhs-ad-spend-tooltip"',
    'id="xhs-ad-leads-chart-wrap"',
    'id="xhs-ad-leads-chart"',
    'id="xhs-ad-leads-tooltip"',
    'xhs-ad-spend-guide',
    'xhs-ad-leads-guide',
    'class="panel xhs-ad-detail-panel"',
    'class="xhs-ad-date-tools xhs-ad-detail-date-tools"',
    'id="xhs-ad-week-month-filter"',
    'id="xhs-ad-week-team-filter"',
    'id="xhs-ad-week-account-filter"',
    'id="xhs-ad-week-owner-filter"',
    'id="xhs-ad-week-summary"',
    'id="xhs-ad-week-account-summary"',
    'id="xhs-ad-week-owner-summary"',
    'id="xhs-ad-week-head"',
    'id="xhs-ad-week-table"',
    'id="xhs-ad-owner-week-head"',
    'id="xhs-ad-owner-week-table"',
    'function xhsAdPrepareWeekControls(',
    'function xhsAdWeekDimension(',
    'function xhsAdWeekPeriods(',
    'function renderXhsAdWeeklyTable(',
    'function renderXhsAdDetailTables(',
    'function bindXhsAdChartHover(',
    'function renderXhsAdSingleChart(',
    'function renderXhsAdChart(',
    'function renderXhsAdFlow()',
    '<h3>归属账号消耗</h3>',
    'xhs-goal-table',
    'function renderXhsAccountStatus()',
    'function xhsGoalCell(',
    '"xhsAccountAudit"',
    '"targetMonth"',
    '"targets"',
    'function renderXhsLeads()',
    'function renderXhsLeadDetails()',
    'id="xhs-traffic"',
    'data-dashboard-view="xhs-traffic"',
    'id="xhs-traffic-updated"',
    'id="xhs-traffic-total"',
    'id="xhs-traffic-paid"',
    'id="xhs-traffic-organic"',
    'id="xhs-traffic-organic-rate"',
    'id="xhs-traffic-spend"',
    'id="xhs-traffic-start-date"',
    'id="xhs-traffic-end-date"',
    'id="xhs-traffic-date-reset"',
    'id="xhs-traffic-table"',
    'id="xhs-traffic-prev-page"',
    'id="xhs-traffic-page-status"',
    'id="xhs-traffic-next-page"',
    'id="xhs-traffic-week-month"',
    'id="xhs-traffic-week-table"',
    'id="xhs-traffic-team-table"',
    'class="panel-note xhs-traffic-footnote"',
    '仅汇总两套数据共同覆盖日期',
    'adContent.ownerRows || []',
    'xhsTrafficState.initialized','function xhsTrafficBuildAccountRows(',
    'function xhsTrafficBuildRows(',
    'organicLeads=totalLeads-paidLeads',
    'function xhsTrafficWeekPeriods(',
    'function renderXhsTrafficWeekTable(',
    'function renderXhsTrafficTeamTable(',
    'function renderXhsTraffic()',
    '"xhsAdFlow"',
    'id="checkout-pressure"',
    'data-dashboard-view="occupancy"',
    '<div class="label">本月退房</div>',
    'id="contract-checkout-definition">退租/（实际退/续租）',
    'function checkoutDisplay(',
    'checkoutActualDepartureCount',
)

FORBIDDEN = (
    'data-dashboard-view="checkout"',
    'id="xhs-lead-inbound-table"',
    'id="xhs-ad-date-reset"',
    '<th>投流账号</th><th>笔记数</th>',
    '<th>归属账号</th><th>笔记数</th>',
    '<div class="label">本月实际退房</div>',
    'id="contract-checkout-renewal"',
    'id="contract-checkout-renewal-previous"',
    '优先按锁房备注识别',
    '剩余房源匹配已付定合同',
    '锁房备注包含“短租”，不限房源状态',
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
    business_trend = payload.get("businessTrend", {})
    business_rows = business_trend.get("rows", [])
    business_dates = [row.get("date") for row in business_rows]
    if len(business_rows) != 30 or business_dates != sorted(business_dates, reverse=True):
        print(f"Business trend date coverage invalid: {len(business_rows)} rows", file=sys.stderr)
        return 1
    if any(
        (date.fromisoformat(business_dates[index]) - date.fromisoformat(business_dates[index + 1])).days != 1
        for index in range(len(business_dates) - 1)
    ):
        print("Business trend dates are not continuous", file=sys.stderr)
        return 1
    if any(int(row.get("newSignCount") or 0) < 0 or int(row.get("reservationCount") or 0) < 0 for row in business_rows):
        print("Business trend contains negative counts", file=sys.stderr)
        return 1
    if not all(business_trend.get("validation", {}).values()):
        print(f"Business trend validation failed: {business_trend.get('validation')}", file=sys.stderr)
        return 1
    checkout_trends = payload.get("checkoutTrends", {})
    as_of = date.fromisoformat(payload.get("dataDate"))
    checkout_specs = (
        ("past", as_of - timedelta(days=29), as_of, True),
        ("future", as_of + timedelta(days=1), as_of + timedelta(days=30), False),
    )
    for key, start, end, newest_first in checkout_specs:
        period = checkout_trends.get(key, {})
        rows = period.get("rows", [])
        dates = [date.fromisoformat(row.get("date")) for row in rows]
        expected = [start + timedelta(days=index) for index in range(30)]
        if newest_first:
            expected.reverse()
        if len(rows) != 30 or dates != expected:
            print(f"Checkout trend date coverage invalid: {key} {len(rows)} rows", file=sys.stderr)
            return 1
        if period.get("startDate") != start.isoformat() or period.get("endDate") != end.isoformat():
            print(f"Checkout trend boundaries invalid: {key}", file=sys.stderr)
            return 1
        if any(int(row.get("checkoutCount") or 0) < 0 for row in rows):
            print(f"Checkout trend contains negative counts: {key}", file=sys.stderr)
            return 1
    if checkout_trends.get("asOfDate") != as_of.isoformat() or not all(checkout_trends.get("validation", {}).values()):
        print(f"Checkout trend validation failed: {checkout_trends.get('validation')}", file=sys.stderr)
        return 1
    future_period = checkout_trends.get("future", {})
    past_period = checkout_trends.get("past", {})
    if future_period.get("sourceFiles") != ["在租中合同.xlsx", "将搬入合同.xlsx"] or future_period.get("dateField") != "退租时间":
        print(f"Future checkout source regression: {future_period.get('sourceFiles')}/{future_period.get('dateField')}", file=sys.stderr)
        return 1
    if past_period.get("sourceFiles") != ["已退租合同.xlsx"] or past_period.get("dateField") != "预退/实退":
        print(f"Past checkout source regression: {past_period.get('sourceFiles')}/{past_period.get('dateField')}", file=sys.stderr)
        return 1
    if not checkout_trends.get("validation", {}).get("occupancyReconciled"):
        print("Future checkout trend does not reconcile with occupancy checkout statistics", file=sys.stderr)
        return 1
    overview_new = payload.get("overviewNew", {})
    expected_priority = ["预定", "将搬入", "锁房"]
    if overview_new.get("priority") != expected_priority:
        print(f"Overview-new priority regression: {overview_new.get('priority')}", file=sys.stderr)
        return 1
    unavailable = int(overview_new.get("unavailableCount") or 0)
    preorder = int(overview_new.get("preorderCount") or 0)
    moving = int(overview_new.get("moveInCount") or 0)
    locked = int(overview_new.get("lockedCount") or 0)
    short_rent = int(overview_new.get("shortRentCount") or 0)
    other_unavailable = int(overview_new.get("otherUnavailableCount") or 0)
    overview_validation = overview_new.get("validation", {})
    if other_unavailable or unavailable != preorder + moving + locked:
        print(f"Overview-new unavailable breakdown invalid: {unavailable}/{preorder}/{moving}/{locked}/{other_unavailable}", file=sys.stderr)
        return 1
    if short_rent > locked or not overview_validation.get("shortRentWithinLocked"):
        print(f"Overview-new short-rent subset invalid: {short_rent}/{locked}", file=sys.stderr)
        return 1
    for period_name in ("currentMonth", "previousMonth"):
        period = payload.get("contractStats", {}).get(period_name, {})
        total = int(period.get("actualCheckoutCount") or 0)
        actual = int(period.get("checkoutActualDepartureCount") or 0)
        renewal = int(period.get("checkoutRenewalCount") or 0)
        if total != actual + renewal:
            print(f"Checkout breakdown does not reconcile: {period_name} {total}/{actual}/{renewal}", file=sys.stderr)
            return 1
    account_audit = payload.get("xhsAccountAudit", {})
    audit_accounts = account_audit.get("accounts", [])
    if len(audit_accounts) != 8 or len({row.get("profile") for row in audit_accounts}) != 8:
        print(f"XHS account audit coverage invalid: {len(audit_accounts)} rows", file=sys.stderr)
        return 1
    if html.count('data-dashboard-view="xhs-traffic"') < 2:
        print("XHS traffic menu missing from desktop or mobile navigation", file=sys.stderr)
        return 1
    if html.count('data-dashboard-view="business-trend"') < 2:
        print("Business trend menu missing from desktop or mobile navigation", file=sys.stderr)
        return 1
    business_section = re.search(r'<section class="section" id="business-trend".*?</section>\s*<section class="section" id="overview"', html, re.S)
    if not business_section or html.count('id="contract-daily-trend-panel"') != 1 or 'id="contract-daily-chart"' not in business_section.group(0):
        print("Contract daily trend chart is not uniquely located in the business-trend view", file=sys.stderr)
        return 1
    if 'data-views="overview"><h3>近期每日合同净变化</h3>' in html:
        print("Contract daily trend chart still exists in the overview view", file=sys.stderr)
        return 1
    if html.count('data-dashboard-view="xhs-note-published"') < 2:
        print("XHS note-published menu missing from desktop or mobile navigation", file=sys.stderr)
        return 1
    if html.count('data-dashboard-view="xhs-lead-details"') < 2:
        print("XHS lead-details menu missing from desktop or mobile navigation", file=sys.stderr)
        return 1
    if html.count('data-dashboard-view="xhs-ad-details"') < 2:
        print("XHS ad-details menu missing from desktop or mobile navigation", file=sys.stderr)
        return 1
    if html.count('data-dashboard-view="customer-data"') < 2:
        print("Customer data menu missing from desktop or mobile navigation", file=sys.stderr)
        return 1
    if html.count('data-dashboard-view="meter-management"') < 2:
        print("Meter-management menu missing from desktop or mobile navigation", file=sys.stderr)
        return 1
    meter = payload.get("meterManagement", {})
    meter_summary = meter.get("summary", {})
    meter_lists = {
        "keepElectric": meter.get("keepElectricDevices", []),
        "negative": meter.get("negativeDevices", []),
        "offline": meter.get("offlineDevices", []),
    }
    if any(int(meter_summary.get(key) or 0) != len(rows) for key, rows in meter_lists.items()):
        print("Meter-management summary does not reconcile", file=sys.stderr)
        return 1
    note_published_rows = payload.get("xhsNotePublished", {}).get("rows", [])
    note_published_keys = {
        (row.get("profile"), row.get("publishedDate"))
        for row in note_published_rows
    }
    if len(note_published_rows) < 232 or len(note_published_keys) != len(note_published_rows):
        print(f"XHS note-published history coverage invalid: {len(note_published_rows)} rows", file=sys.stderr)
        return 1
    if any(
        int(row.get("graphicCount") or 0)
        + int(row.get("videoCount") or 0)
        + int(row.get("pendingCount") or 0)
        != int(row.get("publishedCount") or 0)
        for row in note_published_rows
    ):
        print("XHS note type totals do not reconcile", file=sys.stderr)
        return 1
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
        if (opened_total, copied_total) != (1735, 1119):
            print(f"XHS August target totals invalid: {(opened_total, copied_total)}", file=sys.stderr)
            return 1
        daily_rows = leads.get("dailyRows", [])
        if len(daily_rows) != 168 or len({row.get("date") for row in daily_rows}) != 21:
            print(f"XHS 21-day detail coverage invalid: {len(daily_rows)} rows", file=sys.stderr)
            return 1
    ad_flow = payload.get("xhsAdFlow", {})
    if ad_flow.get("historySource") != "immutable-history/ad-note-daily.csv":
        print(f"XHS ad history source invalid: {ad_flow.get('historySource')}", file=sys.stderr)
        return 1
    ad_dates = [row.get("date") for row in ad_flow.get("accountRows", []) if row.get("date")]
    if not ad_dates or ad_flow.get("date") != ad_flow.get("historyMaxDate") or ad_flow.get("date") != max(ad_dates):
        print(f"XHS ad immutable freshness invalid: {ad_flow.get('date')}/{ad_flow.get('historyMaxDate')}", file=sys.stderr)
        return 1
    if int(ad_flow.get("historyRowCount") or 0) != int(ad_flow.get("totalNoteCount") or 0):
        print("XHS ad immutable row count does not reconcile", file=sys.stderr)
        return 1
    if "noteRows" in ad_flow or "笔记ID" in html or "笔记标题" in html:
        print("XHS ad-note private detail exposure detected", file=sys.stderr)
        return 1
    if ad_flow.get("periodLabel"):
        try:
            start_text, end_text = ad_flow["periodLabel"].split(" 至 ", 1)
            expected_account_days = (date.fromisoformat(end_text) - date.fromisoformat(start_text)).days + 1
            expected_account_days *= 8
        except (TypeError, ValueError):
            print(f"XHS ad history period invalid: {ad_flow.get('periodLabel')}", file=sys.stderr)
            return 1
        if len(ad_flow.get("accountRows", [])) != expected_account_days:
            print(f"XHS ad history account-day coverage invalid: {len(ad_flow.get('accountRows', []))}/{expected_account_days}", file=sys.stderr)
            return 1
        if int(ad_flow.get("unresolvedOwnerCount") or 0):
            print(f"XHS ad history unresolved owners: {ad_flow.get('unresolvedOwnerCount')}", file=sys.stderr)
            return 1
    ad_detail_rows = ad_flow.get("accountRows", [])
    required_ad_detail_keys = {"date", "accountName", "spend", "opened", "leads"}
    if not ad_detail_rows or any(not required_ad_detail_keys.issubset(row) for row in ad_detail_rows):
        print(f"XHS ad-detail history coverage invalid: {len(ad_detail_rows)} rows", file=sys.stderr)
        return 1

    customer = payload.get("customerData", {})
    customer_rows = customer.get("dailyRows", [])
    customer_fields = ("published", "reading", "inbound", "leads", "wechatAdds", "actualTours", "signed", "deposits")
    if customer_rows:
        customer_dates = [row.get("date") for row in customer_rows]
        if len(customer_rows) != 7 or len(set(customer_dates)) != 7 or customer_dates != sorted(customer_dates):
            print(f"Customer data seven-day coverage invalid: {customer_dates}", file=sys.stderr)
            return 1
        if customer.get("startDate") != customer_dates[0] or customer.get("endDate") != customer_dates[-1]:
            print("Customer data period does not match daily rows", file=sys.stderr)
            return 1
        if any(customer.get("totals", {}).get(field) != sum(int(row.get(field) or 0) for row in customer_rows) for field in customer_fields):
            print("Customer data totals do not reconcile", file=sys.stderr)
            return 1

    print(f"dashboard structure valid: {dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
