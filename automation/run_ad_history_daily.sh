#!/usr/bin/env bash
set -euo pipefail

project_dir="${XHS_PROJECT_DIR:-/home/ubuntu/xhs-account-isolation}"
report_date="$(TZ=Asia/Shanghai date -I -d yesterday)"

for _ in $(seq 1 180); do
  if ! systemctl is-active --quiet xhs-leads-collector.service; then
    break
  fi
  sleep 30
done
if systemctl is-active --quiet xhs-leads-collector.service; then
  printf 'Lead collector did not finish within 90 minutes.\n' >&2
  exit 75
fi

"$project_dir/automation/run_ad_history_backfill.sh" "$report_date" "$report_date"
"$project_dir/automation/sync-workbench-data.sh"
