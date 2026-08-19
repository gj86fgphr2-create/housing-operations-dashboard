#!/usr/bin/env bash
set -euo pipefail

project_dir="${XHS_PROJECT_DIR:-/home/ubuntu/xhs-account-isolation}"
collector="${XHS_HISTORY_COLLECTOR:-$project_dir/automation/collect_ad_note_reports.py}"
history_root="${XHS_HISTORY_ROOT:-$project_dir/data/ad-note-history}"
start_date="${1:-2026-07-01}"
end_date="${2:-2026-08-19}"
history_start="${XHS_HISTORY_START_DATE:-2026-07-01}"
profiles=(account-02 account-03 account-04 account-05 account-06 account-07 account-08 account-09)
log="$history_root/backfill.log"
checkpoint="$history_root/checkpoints/backfill.tsv"

install -d -m 0755 "$history_root/raw" "$history_root/checkpoints"
touch "$log" "$checkpoint"

for profile in "${profiles[@]}"; do
  range_zero_json="$history_root/raw/$profile/range-confirmed-zero/ad-note-stats-$start_date-to-$end_date.json"
  if [[ -s "$range_zero_json" ]] && python3 - "$range_zero_json" "$profile" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); p=sys.argv[2]
a=next((row for row in d.get("accounts",[]) if row.get("profile")==p),{})
raise SystemExit(0 if a.get("status")=="ok" and not a.get("rows") else 1)
PY
  then
    printf '%s\t%s\tconfirmed-zero-range\n' "$start_date..$end_date" "$profile" | tee -a "$checkpoint"
    continue
  fi
  requires_daily="$history_root/checkpoints/$profile-requires-daily.txt"
  date_cursor="$start_date"
  while [[ "$date_cursor" < "$end_date" || "$date_cursor" == "$end_date" ]]; do
    if [[ "$start_date" != "$end_date" && -s "$requires_daily" ]] && ! grep -Fxq "$date_cursor" "$requires_daily"; then
      date_cursor="$(date -I -d "$date_cursor + 1 day")"
      continue
    fi
    output_dir="$history_root/raw/$profile/$date_cursor"
    output_json="$output_dir/ad-note-stats-$date_cursor.json"
    if [[ -s "$output_json" ]] && python3 - "$output_json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
raise SystemExit(0 if len(d.get("accounts",[]))==1 and d["accounts"][0].get("status")=="ok" else 1)
PY
    then
      printf '%s\t%s\tskipped-ok\n' "$date_cursor" "$profile" | tee -a "$checkpoint"
      date_cursor="$(date -I -d "$date_cursor + 1 day")"
      continue
    fi
    install -d -m 0755 "$output_dir"
    success=0
    for attempt in 1 2 3; do
      printf '%s [%s %s] attempt %s\n' "$(date --iso-8601=seconds)" "$date_cursor" "$profile" "$attempt" | tee -a "$log"
      if python3 "$collector" --date "$date_cursor" --profiles "$profile" --output-dir "$output_dir" --page-timeout 60 --no-restore >>"$log" 2>&1; then
        success=1
        break
      fi
      sleep $((attempt * 5))
    done
    if [[ "$success" == 1 ]]; then
      printf '%s\t%s\tok\n' "$date_cursor" "$profile" | tee -a "$checkpoint"
    else
      printf '%s\t%s\tfailed\n' "$date_cursor" "$profile" | tee -a "$checkpoint"
    fi
    date_cursor="$(date -I -d "$date_cursor + 1 day")"
  done
done

"$project_dir/accountctl.sh" start account-02 >/dev/null 2>&1 || true
python3 "$project_dir/automation/build_ad_note_history.py" \
  --raw-root "$history_root/raw" \
  --output-root "$history_root" \
  --start-date "$history_start" \
  --end-date "$end_date"
