#!/usr/bin/env bash
set -euo pipefail

project_dir="${XHS_PROJECT_DIR:-/home/ubuntu/xhs-account-isolation}"
collector="$project_dir/automation/collect_ad_note_reports_history_test.py"
history_root="$project_dir/data/ad-note-history"
start_date="${1:-2026-07-01}"
end_date="${2:-2026-08-19}"
profiles=(account-02 account-03 account-04 account-05 account-06 account-07 account-08 account-09)
log="$history_root/corrected-range.log"

for profile in "${profiles[@]}"; do
  output_dir="$history_root/raw/$profile/corrected-range"
  output_json="$output_dir/ad-note-stats-$start_date-to-$end_date.json"
  if [[ -s "$output_json" ]] && python3 - "$output_json" "$profile" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); p=sys.argv[2]
a=next((row for row in d.get("accounts",[]) if row.get("profile")==p),{})
raise SystemExit(0 if a.get("status")=="ok" else 1)
PY
  then
    printf '%s\tskipped-ok\n' "$profile" | tee -a "$log"
    continue
  fi
  install -d -m 0755 "$output_dir"
  success=0
  for attempt in 1 2 3; do
    printf '%s [%s] attempt %s\n' "$(date --iso-8601=seconds)" "$profile" "$attempt" | tee -a "$log"
    if python3 "$collector" --start-date "$start_date" --end-date "$end_date" --profiles "$profile" --output-dir "$output_dir" --page-timeout 60 --no-restore >>"$log" 2>&1; then
      success=1
      break
    fi
    sleep $((attempt * 5))
  done
  if [[ "$success" != 1 ]]; then
    printf '%s\tfailed\n' "$profile" | tee -a "$log"
  fi
done

"$project_dir/accountctl.sh" start account-02 >/dev/null 2>&1 || true
python3 "$project_dir/automation/build_ad_note_history.py" \
  --raw-root "$history_root/raw" \
  --output-root "$history_root" \
  --start-date "$start_date" \
  --end-date "$end_date"
