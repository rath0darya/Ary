#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APP="$ROOT/android/app/src/main"
mkdir -p "$APP/python" "$APP/assets/runtime/web"
cp "$ROOT/ary_episode_watcher.py" "$APP/python/ary_episode_watcher.py"
cp "$ROOT/live_check.py" "$APP/python/live_check.py"
cp "$ROOT/web/"* "$APP/assets/runtime/web/"
# Bundle frontend libraries so the APK UI does not load JavaScript, CSS or fonts from a CDN.
curl -fsSL "https://cdn.jsdelivr.net/npm/hls.js@1.7.3/dist/hls.min.js" -o "$APP/assets/runtime/web/hls.min.js"
curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" -o "$APP/assets/runtime/web/fontawesome.min.css"
mkdir -p "$APP/assets/runtime/webfonts"
for font in fa-solid-900.woff2 fa-regular-400.woff2 fa-brands-400.woff2 fa-v4compatibility.woff2; do
    curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/webfonts/$font" -o "$APP/assets/runtime/webfonts/$font"
done
sed -i \
    -e 's#https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css#/fontawesome.min.css#g' \
    -e 's#https://cdn.jsdelivr.net/npm/hls.js@1.7.3#/hls.min.js#g' \
    "$APP/assets/runtime/web/index.html"
cp "$ROOT/dar-e-nijaat-all-m3u8.json" "$APP/assets/runtime/dar-e-nijaat-all-m3u8.json"
printf '%s\n' "Synchronized Python backend, website and local frontend libraries into Android app sources."
