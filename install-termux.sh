#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
STATE_DIR="${HOME}/.ary-episode-watcher"
mkdir -p "$BIN_DIR" "$STATE_DIR"
cat > "$BIN_DIR/aryweb" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
APP_DIR="$APP_DIR"
STATE_DIR="${HOME}/.ary-episode-watcher"
mkdir -p "\$STATE_DIR"
PID_FILE="\$STATE_DIR/aryweb.pid"
LOG_FILE="\$STATE_DIR/aryweb.log"
PORT="\${ARYWEB_PORT:-8787}"

server_responds() {
  python3 - "\$PORT" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:"+sys.argv[1]+"/api/health", timeout=1.5) as r:
        raise SystemExit(0 if r.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
}

wake_lock() { command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1 || true; }
wake_unlock() { command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock >/dev/null 2>&1 || true; }
running_pid() { if [[ -f "\$PID_FILE" ]]; then local p; p="$(cat "\$PID_FILE" 2>/dev/null || true)"; [[ -n "\$p" ]] && kill -0 "\$p" 2>/dev/null && return 0; fi; return 1; }

case "\${1:-}" in
  --foreground|-f)
    wake_lock
    trap 'wake_unlock' EXIT INT TERM
    set +e
    python3 "\$APP_DIR/ary_episode_watcher.py"
    rc=\$?
    wake_unlock
    trap - EXIT INT TERM
    exit "\$rc"
    ;;
  --stop)
    if running_pid; then
      p="$(cat "\$PID_FILE")"; kill "\$p" 2>/dev/null || true
      for _ in 1 2 3 4 5; do kill -0 "\$p" 2>/dev/null || break; sleep 1; done
      rm -f "\$PID_FILE"
      echo "ARY WebUI stop requested."
    elif server_responds; then
      echo "ARY WebUI is running in a different/foreground session; stop it with Ctrl+C there."
    else
      echo "ARY WebUI is not running."
      rm -f "\$PID_FILE"
    fi
    wake_unlock
    exit 0
    ;;
  --status)
    if running_pid || server_responds; then
      echo "ARY WebUI responds at http://127.0.0.1:\$PORT"
      [[ -f "\$LOG_FILE" ]] && echo "Log: \$LOG_FILE"
      exit 0
    fi
    echo "ARY WebUI is not running."
    exit 1
    ;;
  --logs|-l)
    touch "\$LOG_FILE"
    exec tail -n 100 -f "\$LOG_FILE"
    ;;
  --restart)
    "\$0" --stop || true
    sleep 1
    ;;
  "")
    ;;
  *)
    echo "Usage: aryweb [--status|--stop|--restart|--logs|--foreground]"
    exit 2
    ;;
esac

if server_responds; then
  echo "ARY WebUI is already responding at http://127.0.0.1:\$PORT"
  echo "Use 'aryweb --status' or 'aryweb --logs'."
  exit 0
fi
wake_lock
touch "\$LOG_FILE"
nohup python3 "\$APP_DIR/ary_episode_watcher.py" >> "\$LOG_FILE" 2>&1 </dev/null &
pid=\$!
echo "\$pid" > "\$PID_FILE"
sleep 1
if server_responds; then
  echo "ARY WebUI started in background."
  echo "Open: http://127.0.0.1:\$PORT"
  echo "Status: aryweb --status"
  echo "Logs:   aryweb --logs"
  echo "Stop:   aryweb --stop"
else
  echo "ARY WebUI did not become ready. Recent log:"
  tail -n 50 "\$LOG_FILE" || true
  exit 1
fi
EOF
cat > "$BIN_DIR/arymenu" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec python3 "$APP_DIR/termux_menu.py" "\$@"
EOF
chmod +x "$BIN_DIR/aryweb" "$BIN_DIR/arymenu"
echo "Installed commands:"
echo "  aryweb         - start the WebUI in the background with optional wake lock"
echo "  aryweb --logs  - follow server logs"
echo "  aryweb --stop  - stop the background server"
echo "  aryweb -f      - run in the foreground"
echo "  arymenu        - open the interactive Termux menu"
echo "Web URL: http://127.0.0.1:${ARYWEB_PORT:-8787}"
