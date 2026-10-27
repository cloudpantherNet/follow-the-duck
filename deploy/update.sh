#!/usr/bin/env bash
# Update the robot to the latest code WITHOUT touching configuration.
#
#   cd ~/follow-the-duck && ./deploy/update.sh
#
# Never asks for anything and never modifies .env — credentials entered once
# stay put. The package is installed editable, so a git pull is the update;
# pip runs only when pyproject.toml changed, and the ftd service (if
# installed and running) is restarted so the new code takes effect.

set -euo pipefail
cd "$(dirname "$0")/.."

[ -x .venv/bin/ftd ] || {
  echo "no .venv found — run ./deploy/install.sh once first"; exit 1; }

before="$(git rev-parse HEAD)"
git pull --ff-only
after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  echo "already up to date ($(git rev-parse --short HEAD))"
else
  echo "updated $(git rev-parse --short "$before") -> $(git rev-parse --short "$after")"
  if git diff --name-only "$before" "$after" | grep -q '^pyproject\.toml$'; then
    echo "pyproject.toml changed — reinstalling package"
    .venv/bin/pip install -q -e .
  fi
fi

if command -v systemctl >/dev/null && systemctl is-enabled --quiet ftd 2>/dev/null; then
  echo "restarting ftd service"
  sudo systemctl restart ftd
  systemctl --no-pager --lines 0 status ftd | head -3 || true
else
  echo "ftd service not installed; nothing to restart"
fi
