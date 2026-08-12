#!/usr/bin/env bash
set -euo pipefail

repo_raw="https://raw.githubusercontent.com/gj86fgphr2-create/housing-operations-dashboard/main"
app="/opt/yuxiaor-automation/app"
site="/opt/yuxiaor-automation/site"
bot_app="/opt/yuxiaor-aibot"
stamp="$(date +%Y%m%d-%H%M%S)"

sudo install -d -o ubuntu -g ubuntu "$app" "$site"
sudo cp /etc/systemd/system/yuxiaor-download.service "/etc/systemd/system/yuxiaor-download.service.$stamp.bak"
curl --fail --silent --show-error --retry 5 "$repo_raw/automation/generate_full_dashboard.py" -o /tmp/generate_full_dashboard.py
curl --fail --silent --show-error --retry 5 "$repo_raw/automation/latest-dashboard-template.html" -o /tmp/latest-dashboard-template.html
curl --fail --silent --show-error --retry 5 "$repo_raw/automation/finalize_export.py" -o /tmp/finalize_export.py
curl --fail --silent --show-error --retry 5 "$repo_raw/automation/aibot-server.mjs" -o /tmp/aibot-server.mjs
sudo install -o ubuntu -g ubuntu -m 0755 /tmp/generate_full_dashboard.py "$app/generate_full_dashboard.py"
sudo install -o ubuntu -g ubuntu -m 0644 /tmp/latest-dashboard-template.html "$app/latest-dashboard-template.html"
sudo install -o ubuntu -g ubuntu -m 0755 /tmp/finalize_export.py "$app/finalize_export.py"
sudo install -o ubuntu -g ubuntu -m 0644 /tmp/aibot-server.mjs "$bot_app/aibot-server.mjs"

sudo python3 - <<'PY'
from pathlib import Path
p=Path('/etc/systemd/system/yuxiaor-download.service')
s=p.read_text()
line='ExecStartPost=/usr/bin/python3 /opt/yuxiaor-automation/app/generate_full_dashboard.py /opt/yuxiaor-automation/data/current /opt/yuxiaor-automation/app/latest-dashboard-template.html /opt/yuxiaor-automation/site/index.html /opt/yuxiaor-automation/site/index.html'
old='ExecStartPost=/usr/bin/python3 /opt/yuxiaor-automation/app/generate_dashboard.py'
if line not in s:
    if old not in s: raise SystemExit('generate_dashboard ExecStartPost not found')
    s=s.replace(old, old+'\n'+line)
p.write_text(s)
PY

sudo python3 - <<'PY'
from pathlib import Path
p=Path('/etc/systemd/system/yuxiaor-download.service')
s=p.read_text()
old='ExecStartPost=/usr/bin/python3 /opt/yuxiaor-automation/app/finalize_export.py /opt/yuxiaor-automation/data/current /opt/yuxiaor-automation/notifications/pending'
new=old+' /opt/yuxiaor-automation/site/index.html https://gj86fgphr2-create.github.io/housing-operations-dashboard/#operations-brief'
if new not in s:
    if old not in s: raise SystemExit('finalize_export ExecStartPost not found')
    s=s.replace(old,new)
p.write_text(s)
PY

sudo systemctl daemon-reload
sudo systemctl restart yuxiaor-aibot.service
sudo systemctl enable --now yuxiaor-download.timer
sudo systemctl start yuxiaor-download.service
sudo systemctl is-active yuxiaor-download.timer
sudo grep -q 'data-dashboard-view="performance"' "$site/index.html"
sudo grep -q '5%以下绿色' "$site/index.html"
curl --fail --silent --show-error http://127.0.0.1/dashboard-sync/index.html | grep -q '"dataDate"'
echo FULL_DASHBOARD_DEPLOYED

