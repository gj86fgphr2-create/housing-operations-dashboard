#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="$project_dir/data"
lead_dir="$data_dir/lead-stats"
target="${XHS_WORKBENCH_TARGET:-ubuntu@43.128.67.69}"
remote_data_root="${XHS_WORKBENCH_DATA_ROOT:-/opt/xhs-account-isolation/data}"
identity_file="${XHS_WORKBENCH_IDENTITY_FILE:-/home/ubuntu/.ssh/xhs_dashboard_sync_ed25519}"
registry_file="$data_dir/note-id-registry/xhs-note-ids.xlsx"

content_dir="$(find "$data_dir" -mindepth 1 -maxdepth 1 -type d \
  -name 'content-stats-*-to-*' -printf '%T@ %p\n' \
  | sort -nr | head -n 1 | cut -d' ' -f2-)"

if [[ -z "$content_dir" ]]; then
  printf 'No dated content-statistics directory found in %s\n' "$data_dir" >&2
  exit 1
fi

lead_files=(
  "$lead_dir/latest.json"
  "$lead_dir/latest.csv"
  "$lead_dir/weekly-summary-latest.json"
  "$lead_dir/weekly-summary-latest.csv"
  "$lead_dir/today-yesterday-latest.csv"
  "$lead_dir/account-time-latest.json"
  "$lead_dir/account-time-latest.csv"
)
content_files=(
  "$content_dir/latest.json"
  "$content_dir/daily-reading-latest.csv"
  "$content_dir/note-cumulative-latest.csv"
  "$content_dir/content-weekly-summary-latest.json"
  "$content_dir/content-weekly-summary-latest.csv"
)

for path in "${lead_files[@]}" "${content_files[@]}" "$registry_file"; do
  if [[ ! -f "$path" ]]; then
    printf 'Required synchronization file is missing: %s\n' "$path" >&2
    exit 1
  fi
done

ssh_options=(
  -i "$identity_file"
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=yes
)
remote_content_dir="$remote_data_root/$(basename "$content_dir")"
remote_registry_dir="$remote_data_root/note-id-registry"

ssh "${ssh_options[@]}" "$target" \
  "install -d -m 0755 '$remote_data_root/lead-stats' '$remote_content_dir' '$remote_registry_dir'"

rsync -a -e "ssh -i $identity_file -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes" \
  "${lead_files[@]}" "$target:$remote_data_root/lead-stats/"
rsync -a -e "ssh -i $identity_file -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes" \
  "${content_files[@]}" "$target:$remote_content_dir/"
rsync -a -e "ssh -i $identity_file -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes" \
  "$registry_file" "$target:$remote_registry_dir/"

ssh "${ssh_options[@]}" "$target" \
  "/usr/bin/python3 /opt/yuxiaor-automation/app/generate_full_dashboard.py /opt/yuxiaor-automation/data/current /opt/yuxiaor-automation/app/latest-dashboard-template.html /opt/yuxiaor-automation/site/index.html /opt/yuxiaor-automation/site/index.html && /usr/bin/python3 /opt/yuxiaor-automation/app/validate_dashboard_structure.py /opt/yuxiaor-automation/site/index.html"

printf 'Synchronized Xiaohongshu summaries from %s to %s:%s\n' \
  "$(basename "$content_dir")" "$target" "$remote_data_root"
