#!/usr/bin/env bash
set -euo pipefail

collector_root="${WTYZ_COLLECTOR_ROOT:-/home/ubuntu/wtyz-meter-collector}"
data_dir="$collector_root/data"
target="${WTYZ_WORKBENCH_TARGET:-ubuntu@43.128.67.69}"
identity_file="${WTYZ_WORKBENCH_IDENTITY_FILE:-/home/ubuntu/.ssh/xhs_dashboard_sync_ed25519}"
remote_data_dir="${WTYZ_WORKBENCH_DATA_DIR:-/opt/yuxiaor-automation/data/meter-management}"

files=("$data_dir/status.json")
if [[ -f "$data_dir/latest.json" ]]; then
  files+=("$data_dir/latest.json")
fi
if [[ -f "$data_dir/offline-history.json" ]]; then
  files+=("$data_dir/offline-history.json")
fi

ssh_options=(
  -i "$identity_file"
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=yes
)

ssh "${ssh_options[@]}" "$target" "install -d -m 0755 '$remote_data_dir'"
rsync -a -e "ssh -i $identity_file -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes" \
  "${files[@]}" "$target:$remote_data_dir/"

ssh "${ssh_options[@]}" "$target" \
  "/usr/bin/python3 /opt/yuxiaor-automation/app/enqueue_meter_collection.py && /usr/bin/python3 /opt/yuxiaor-automation/app/generate_full_dashboard.py /opt/yuxiaor-automation/data/current /opt/yuxiaor-automation/app/latest-dashboard-template.html /opt/yuxiaor-automation/site/index.html /opt/yuxiaor-automation/site/index.html && /usr/bin/python3 /opt/yuxiaor-automation/app/validate_dashboard_structure.py /opt/yuxiaor-automation/site/index.html && test \"\$(stat -c '%U:%G' /opt/yuxiaor-automation/site/index.html)\" = 'ubuntu:ubuntu'"

# Personal-WeChat delivery is isolated from the existing dashboard and WeCom flow.
# A delivery failure is logged remotely but must not fail the stable synchronization task.
if ! ssh "${ssh_options[@]}" "$target" \
  "/usr/bin/python3 /opt/yuxiaor-automation/app/send_youmi_weixin_report.py"; then
  printf 'Youmi personal-WeChat report failed; original synchronization remains successful.\n' >&2
fi

printf 'Synchronized sanitized meter-management data to %s:%s\n' "$target" "$remote_data_dir"
