#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="\${PREFIX:-/data/data/com.termux/files/usr}/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/aryweb" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec python3 "$APP_DIR/ary_episode_watcher.py" "\$@"
EOF
cat > "$BIN_DIR/arymenu" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec python3 "$APP_DIR/termux_menu.py" "\$@"
EOF
chmod +x "$BIN_DIR/aryweb" "$BIN_DIR/arymenu"
echo "Installed commands:"
echo "  aryweb  - start the responsive ARY+ web interface"
echo "  arymenu - open the interactive Termux menu"
echo "Web URL: http://127.0.0.1:\${ARYWEB_PORT:-8787}"
