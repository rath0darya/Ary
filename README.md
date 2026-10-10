# Dar-E-Nijaat Episode Watcher

Termux-friendly Dar-E-Nijaat episode interface with an in-app player and download manager. Playback and downloading require an accessible direct media source you are authorized to use; an episode webpage URL is not itself a video stream.

## Features

- Series discovery and metadata enrichment from configured catalogue sources.
- Dar-E-Nijaat uses a canonical repository mapping of 20 real episode IDs and saved HLS source URLs; other catalogue entries are discovered at runtime.
- Inspects the HLS master playlist and exposes every advertised resolution.
- Automatically selects the highest advertised video resolution.
- Downloads with FFmpeg stream copy; no quality-lowering re-encode.
- Verifies the resulting MP4 with ffprobe.
- Background download jobs with progress/status API.
- OTT-style cinematic home page with a featured hero, horizontal poster rails, mobile-first layouts, and category/genre filters.
- Local search across title, ID, year, type, genre, description and cast, plus year/genre/type/sort filters.
- Premium, native-style transparent player overlay: double-tap left/right to skip 10 seconds, a timestamp-matched video preview while scrubbing, quality and speed controls, lock/unlock touch controls, orientation and fullscreen toggles, auto-hide overlays, and resume playback.
- Episode cards provide prominent in-app Stream and Download buttons; playlist/quality inspection is a secondary action.
- Stream and download actions use the backend `/api/stream-info` and `/api/download` endpoints; downloads appear in the in-page manager and are saved to the device by tapping **Save file to device**.
- Browser HLS playback using HLS.js when the source permits browser playback.
- No external AI/API service.
- API key is read from ARY_API_KEY or ~/.ary-episode-watcher/api-key.txt; it is never displayed.
- Browser catalogue and episode state is cached for the current tab so returning from another app can reuse saved data instead of flashing an empty loading screen. Hiding the tab saves playback position without incorrectly marking the episode as finished.
- Designed for Android/Termux, with optional Termux wake-lock support and a background server mode.

Use the downloader only for media you are authorized to download. The website uses an in-app player and does not redirect users to provider-branded playback pages.

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
    live_check.py
    web/index.html
    requirements.txt
    tests/test_core.py
    tests/test_dar_e_nijaat.py
    dar-e-nijaat-all-m3u8.json
    run.sh
    .gitignore


## Modern web UI and Termux menu

The responsive web interface includes catalogue search and filters, poster art with proxy/direct-image fallback, a premium in-app player, a download manager panel, live size/speed updates, and optional browser notifications. The player keeps settings inside the transparent video overlay and does not expose external provider redirect buttons. Download notifications require granting permission in the browser; the **Save file to device** link still needs a user tap so Android/browser download handling can take over. The tool does not record the live broadcast.

Install the Termux commands from the repository directory:

    chmod +x install-termux.sh
    ./install-termux.sh

Then use:

    arymenu

Or start the WebUI directly in the background:

    aryweb

Open http://127.0.0.1:8787 in the browser. The command supports `aryweb --status`, `aryweb --logs`, `aryweb --restart`, `aryweb --stop`, and `aryweb --foreground`. When the Termux:API command `termux-wake-lock` is installed, the launcher requests a wake lock to reduce CPU suspension while the server runs in the background. Android battery restrictions, force-stop, or OEM process killing can still stop a local server; disable battery optimization for Termux if background reliability matters. The menu also provides server health, catalogue refresh, a download-folder listing, and the local web address.


## Standalone Android APK (no Termux)

The repository includes a standalone **CineWave** Android app project under `android/`. It bundles the CineWave interface and Python backend into the APK and runs the backend locally inside the app. The app uses an embedded Python runtime and a native Android FFmpegKit dependency for supported downloads.

To build without a PC, open the repository's **Actions** tab, choose **Build CineWave Android APK**, and select **Run workflow**. Download the `cinewave-android-apk` artifact from the completed run, extract it, and install `app-debug.apk`. Uninstall an earlier debug APK first if Android rejects the update, since each workflow build uses a temporary signing key. The workflow artifact is temporary (7 days); no permanent APK release or hosting page is created.

The APK still needs internet for online catalogue and media sources. Its cache and working downloads are app-private; downloaded files can be exported to Android Downloads from the app. Android 7.0 or newer and a 64-bit ARM device (arm64-v8a) are supported by this build configuration.

See [android/README.md](android/README.md) for build details.
