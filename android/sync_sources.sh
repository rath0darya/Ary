#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APP="$ROOT/android/app/src/main"
mkdir -p "$APP/python" "$APP/assets/runtime/web"
cp "$ROOT/ary_episode_watcher.py" "$APP/python/ary_episode_watcher.py"
cp "$ROOT/live_check.py" "$APP/python/live_check.py"
cp "$ROOT/web/"* "$APP/assets/runtime/web/"
cp "$ROOT/dar-e-nijaat-all-m3u8.json" "$APP/assets/runtime/dar-e-nijaat-all-m3u8.json"
printf '%s\n' "Synchronized Python backend and website into Android app sources."
