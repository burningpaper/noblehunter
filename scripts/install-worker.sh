#!/usr/bin/env bash
# The Noble Hunter runner as a launchd LaunchAgent: it starts when you log in and restarts if it stops.
#
#   scripts/install-worker.sh install     check settings, write the agent and start it
#   scripts/install-worker.sh status      is it running? plus the latest log lines
#   scripts/install-worker.sh uninstall   stop it and remove the agent
set -euo pipefail

LABEL="com.noblehunter.worker"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/noble-hunter"
DOMAIN="gui/$(id -u)"

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  printf '%s' "$value"
}

install_agent() {
  local uv
  uv="$(command -v uv || true)"
  if [ -z "$uv" ]; then
    echo "uv isn't on your PATH. Install it first: https://docs.astral.sh/uv/" >&2
    exit 1
  fi

  echo "Checking settings and the database..."
  (cd "$REPO" && "$uv" run --group pipeline python -m pipeline.cli worker --check)

  mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLIST_XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml_escape "$uv")</string>
    <string>run</string>
    <string>--group</string>
    <string>pipeline</string>
    <string>python</string>
    <string>-m</string>
    <string>pipeline.cli</string>
    <string>worker</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml_escape "$REPO")</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$(xml_escape "$(dirname "$uv")"):/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$(xml_escape "$LOG_DIR/launchd.out.log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml_escape "$LOG_DIR/launchd.err.log")</string>
</dict>
</plist>
PLIST_XML

  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  # bootout returns before the old runner has exited, and a runner stopped mid-run takes a while
  # to close its browser. Loading the new one too soon fails with "Bootstrap failed: 5".
  for _ in $(seq 1 60); do
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
    sleep 1
  done
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "Installed and started."
  echo "Logs: $LOG_DIR/worker.log"
  echo "Check on it any time: scripts/install-worker.sh status"
}

show_status() {
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl print "$DOMAIN/$LABEL" | grep -E "^[[:space:]]*(state|pid|last exit code) =" || true
  else
    echo "Not installed. Run: scripts/install-worker.sh install"
  fi
  if [ -f "$LOG_DIR/worker.log" ]; then
    echo "--- latest log lines ($LOG_DIR/worker.log)"
    tail -n 15 "$LOG_DIR/worker.log"
  fi
}

uninstall_agent() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Stopped and removed."
}

case "${1:-}" in
  install) install_agent ;;
  status) show_status ;;
  uninstall) uninstall_agent ;;
  *)
    echo "Usage: $0 install|status|uninstall" >&2
    exit 2
    ;;
esac
