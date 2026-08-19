#!/usr/bin/env bash
set -euo pipefail

project_dir="${XHS_PROJECT_DIR:-/home/ubuntu/xhs-account-isolation}"
collector="$project_dir/automation/collect_ad_note_reports_history_test.py"
history_root="$project_dir/data/ad-note-history"
start_date="${1:-2026-07-01}"
end_date="${2:-2026-08-19}"
profiles=(account-02 account-03 account-04 account-05 account-06 account-07 account-08 account-09)
log="$history_root/range-preflight.log"

for profile in "${profiles[@]}"; do
  staging="$history_root/range-preflight/$profile"
  install -d -m 0755 "$staging"
  source_json="$staging/ad-note-stats-$start_date-to-$end_date.json"
  if [[ ! -s "$source_json" ]]; then
    if ! python3 "$collector" --start-date "$start_date" --end-date "$end_date" --profiles "$profile" --output-dir "$staging" --page-timeout 60 --no-restore >>"$log" 2>&1; then
      printf '%s\tfailed\n' "$profile" | tee -a "$log"
      continue
    fi
  fi
  row_count="$(python3 - "$source_json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); print(sum(len(a.get("rows") or []) for a in d.get("accounts",[])))
PY
)"
  if [[ "$row_count" == 0 ]]; then
    target="$history_root/raw/$profile/range-confirmed-zero"
    install -d -m 0755 "$target"
    install -m 0644 "$source_json" "$target/$(basename "$source_json")"
    printf '%s\tconfirmed-zero-range\n' "$profile" | tee -a "$log"
  else
    target="$history_root/raw/$profile/range-coverage"
    install -d -m 0755 "$target"
    python3 - "$source_json" "$target/$(basename "$source_json")" "$history_root/checkpoints/$profile-requires-daily.txt" <<'PY'
import json,sys
source,target,dates_path=sys.argv[1:]
d=json.load(open(source))
dates=sorted({row.get("date") for account in d.get("accounts",[]) for row in account.get("rows",[]) if row.get("date")})
for account in d.get("accounts",[]):
    account["range_discovery_row_count"]=len(account.get("rows") or [])
    account["rows"]=[]
with open(target,"w",encoding="utf-8") as stream:
    json.dump(d,stream,ensure_ascii=False,indent=2)
    stream.write("\n")
with open(dates_path,"w",encoding="utf-8") as stream:
    stream.writelines(day+"\n" for day in dates)
PY
    printf '%s\trequires-daily\t%s rows\n' "$profile" "$row_count" | tee -a "$log"
  fi
done

"$project_dir/accountctl.sh" start account-02 >/dev/null 2>&1 || true
