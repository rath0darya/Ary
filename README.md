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
- Manual ARY Digital Live availability check from the website interface whenever you choose.
- No external AI/API service.
- API key is read from ARY_API_KEY or ~/.ary-episode-watcher/api-key.txt; it is never displayed.
- Designed for Android/Termux.

Use the downloader only for media you are authorized to download. The live check is manual only: it does not schedule checks, run in the background, or record/archive the live broadcast.

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

## Manual ARY Digital live check

Open the website and press **Check now** in the ARY Digital Live panel whenever you want to check the broadcast. The tool checks the public live page for an accessible HLS playlist and reports the result. It does not run a scheduler, poll in the background, or record the stream. If the page loads its stream through a dynamic player API, the check may say it cannot discover the playlist even while the official player works.

Official page: https://live.arydigital.tv/

## API

- GET /api/health
- GET /api/live/check (manual, on-demand check; no scheduling)
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
    live_check.py
    web/index.html
    requirements.txt
    tests/test_core.py
    run.sh
    .gitignore


## Modern web UI and Termux menu

The responsive web interface includes catalogue search and filters, poster art, an in-page ARY Digital Live viewer (the official page may block embedding in some browsers), a download manager panel, live size/speed updates, and optional browser notifications. Download notifications require granting permission in the browser; the **Save file to device** link still needs a user tap so Android/browser download handling can take over. The tool does not record the live broadcast.

Install the Termux commands from the repository directory:

    chmod +x install-termux.sh
    ./install-termux.sh

Then use:

    arymenu

Choose **Start / restart web interface** or start it directly with:

    aryweb

Open http://127.0.0.1:8787 in the browser. The menu also provides server health, catalogue refresh, a download-folder listing, and the local web address. The menu uses only Python's standard library and adapts to narrow terminal widths.
