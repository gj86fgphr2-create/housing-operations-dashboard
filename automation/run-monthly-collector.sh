#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="$project_dir/data/lead-stats"
end_date="$(date +%F)"
start_date="$(date -d "$(date +%Y-%m-01) -1 month" +%F)"
content_output_dir="$project_dir/data/content-stats-$start_date-to-$end_date"
start_epoch="$(date -d "$start_date" +%s)"
end_epoch="$(date -d "$end_date" +%s)"
days="$(( (end_epoch - start_epoch) / 86400 + 1 ))"

set +e
/usr/bin/python3 "$project_dir/automation/collect_lead_stats.py" \
  --days "$days" \
  --end-date "$end_date" \
  --output-dir "$output_dir"
lead_status=$?
set -e
if [[ "$lead_status" -ne 0 && "$lead_status" -ne 2 ]]; then
  exit "$lead_status"
fi
if [[ "$lead_status" -eq 2 ]]; then
  printf 'Warning: lead collector completed with partial account errors; continuing with available data.\n' >&2
fi

/usr/bin/python3 "$project_dir/automation/summarize_weekly.py" \
  --input "$output_dir/latest.json" \
  --output-dir "$output_dir"

set +e
/usr/bin/python3 "$project_dir/automation/collect_content_stats.py" \
  --start-date "$start_date" \
  --end-date "$end_date" \
  --output-dir "$content_output_dir"
content_status=$?
set -e
if [[ "$content_status" -ne 0 && "$content_status" -ne 2 ]]; then
  exit "$content_status"
fi
if [[ "$content_status" -eq 2 ]]; then
  printf 'Warning: content collector completed with partial account errors; continuing with available data.\n' >&2
fi

/usr/bin/python3 "$project_dir/automation/summarize_content_weekly.py" \
  --input "$content_output_dir/latest.json" \
  --output-dir "$content_output_dir"

/usr/bin/python3 "$project_dir/automation/update_note_id_registry.py" \
  --input "$content_output_dir/latest.json" \
  --template "$project_dir/automation/xhs-note-ids-template.xlsx" \
  --output "$project_dir/data/note-id-registry/xhs-note-ids.xlsx" \
  --window-days 30
