# ARY Episode Watcher

Standalone Termux-friendly ARY episode catalogue, HLS inspector, and authorized media downloader.

## Features

- Dynamic ARY Plus series discovery across ARY Plus catalogue genres.
- No hard-coded series ID or episode ID; series and episodes are discovered at runtime.
- Inspects the HLS master playlist and exposes every advertised resolution.
- Automatically selects the highest advertised video resolution.
- Downloads with FFmpeg stream copy; no quality-lowering re-encode.
- Verifies the resulting MP4 with ffprobe.
- Background download jobs with progress/status API.
- Netflix/Prime-style home catalogue with All, Drama, Telefilms, Shows, Movies, Comedy, Romance, Action, Sports, News and metadata-driven genre rows.
- Local search across title, ID, year, type, genre, description and cast, plus year/genre/type/sort filters.
- Responsive MX-style player controls with auto-hide UI, tap-to-show controls, double-tap seek, fullscreen, portrait/landscape orientation support where the browser permits it, quality/speed/fit/caption settings, and resume playback.
- Clickable ARY Plus fallback links for every discovered series.
- Browser HLS playback using HLS.js when the source permits browser playback.
- Scheduled Friday/Saturday live availability monitoring at 8:30 PM India time.
- No external AI/API service.
- API key is read from ARY_API_KEY or ~/.ary-episode-watcher/api-key.txt; it is never displayed.
- Designed for Android/Termux.

Use the downloader only for media you are authorized to download. The live scheduler monitors availability and does not record or archive the live broadcast.

## Requirements

Termux packages:

    pkg update
    pkg install python ffmpeg

Optional Android storage permission:

    termux-setup-storage

Python dependencies:

    pip install -r requirements.txt

## Run the website

    python ary_episode_watcher.py

Then open http://127.0.0.1:8787

For a custom port:

    ARYWEB_PORT=8787 python ary_episode_watcher.py

## Scheduled ARY Digital live monitor

The monitor checks the ARY Digital live page every Friday and Saturday starting at **8:30 PM Asia/Kolkata**. It waits for an HLS playlist to become available, logs when a live playlist is detected, and reports when repeated checks suggest the broadcast may have ended. It does **not** save video or audio.

Start it in a separate Termux session:

    cd ~/ary/Ary
    python3 live_schedule.py

Leave that session running and keep the device powered, online, and awake. The log is written to:

    ~/.ary-episode-watcher/live-monitor.jsonl

Optional settings:

    ARY_LIVE_POLL_SECONDS=30
    ARY_LIVE_ACTIVE_POLL_SECONDS=20
    ARY_LIVE_END_FAILURES=5
    ARY_LIVE_PAGE=https://live.arydigital.tv/

The monitor currently discovers HLS playlist URLs from the page HTML. If ARY builds its player URL dynamically in JavaScript or changes its stream technology, it may report that no playlist was found; this must be verified on an actual live broadcast. It never logs signed media URLs.

## API

- GET /api/health
- GET /api/series
- GET /api/episodes?series=<series-id>
- GET /api/stream-info?series=<series-id>&episode=<episode-id>
- GET /api/download?series=<series-id>&episode=<episode-id>&number=<n>&title=<title>
- GET /api/download/<job-id>
- GET /api/catalogue/metadata?start=1
- GET /api/catalogue/metadata

The download endpoint intentionally has no quality parameter. The server chooses the highest HLS variant advertised by the source.

## Storage

Default output directory:

    /sdcard/Movies/ARY Episode Watcher

Override with:

    export ARY_DOWNLOAD_DIR="$HOME/storage/movies/ARY Episode Watcher"

## API key

Preferred:

    export ARY_API_KEY='your-key'

or:

    mkdir -p ~/.ary-episode-watcher
    printf '%s\n' 'your-key' > ~/.ary-episode-watcher/api-key.txt
    chmod 600 ~/.ary-episode-watcher/api-key.txt

The key is never written into the repository.

## Notes about quality

The app selects the highest HLS resolution, but resolution alone does not guarantee visually superior source quality. The stream inspector reports width, height, advertised bandwidth, average bandwidth, codec, and playlist URL so the selected source can be verified. FFmpeg uses stream copy and therefore does not improve an already-compressed source.

## Project layout

    ary_episode_watcher.py
    live_schedule.py
    web/index.html
    requirements.txt
    tests/test_core.py
    run.sh
    .gitignore
