<div align="center">

<img src="docs/banner.svg" alt="CineWave — native Android streaming companion" width="100%">

### A native Android experience for stories worth staying up for.

**[Project site](https://rath0darya.github.io/Ary/)** · **[Download the latest APK](https://github.com/rath0darya/Ary/releases/latest/download/CineWave.apk)** · **[All releases](https://github.com/rath0darya/Ary/releases)**

[![Android APK](https://img.shields.io/badge/Android-Download%20APK-35c878?style=for-the-badge&logo=android&logoColor=white)](https://github.com/rath0darya/Ary/releases/latest/download/CineWave.apk)
[![Latest release](https://img.shields.io/github/v/release/rath0darya/Ary?display_name=tag&style=for-the-badge&color=ff416b)](https://github.com/rath0darya/Ary/releases)

</div>

## CineWave

CineWave is a native Android media companion with an embedded Python service. It starts with a bundled catalogue entry, then discovers more titles in the background so the home screen is available without waiting for a full crawl. A lightweight responsive web interface is also included for desktop and mobile browsers.

## What it does

- Native Android UI with catalogue search, categories, title details and episode browsing.
- Embedded on-device Python API; no separate server setup is required by the Android app.
- Background catalogue discovery with local persistent cache and immediate first-launch content.
- Media3 playback with HLS support, resume position, playback speed and seeking controls.
- Download jobs and stream inspection through the local API.
- Responsive browser UI with catalogue and playback tools.
- Android APKs are published to the stable [CineWave Android release](https://github.com/rath0darya/Ary/releases/latest).

## Install on Android

Download [`CineWave.apk`](https://github.com/rath0darya/Ary/releases/latest/download/CineWave.apk) from the latest release and install it. New releases use a persistent signing key and can update that installation in place.

**One-time upgrade note:** APKs published before stable signing used temporary build keys. Android will reject an update over one of those older installs with “App not installed as package conflicts with an existing package.” Uninstall that old app once, then install the current APK. Your Android system may remove the app’s private data when it is uninstalled.

## Run the browser app with Python

Requirements: Python 3.10+ and FFmpeg. Install dependencies and start the bundled service:

```sh
python -m pip install -r requirements.txt
python ary_episode_watcher.py
```

Then open <http://127.0.0.1:8787>. To choose another port, set `ARYWEB_PORT`, for example `ARYWEB_PORT=8788 python ary_episode_watcher.py`.

## API

- `GET /api/health`
- `GET /api/series` — returns the current catalogue immediately; discovery status is in `discovery.state`.
- `GET /api/series/detail?series=<series-id>`
- `GET /api/episodes?series=<series-id>`
- `GET /api/stream-info?series=<series-id>&episode=<episode-id>`
- `GET /api/download?series=<series-id>&episode=<episode-id>&number=<n>&title=<title>`
- `GET /api/download/<job-id>`
- `GET /api/catalogue/metadata?start=1`

The download endpoint chooses the highest HLS variant advertised by the source. Use it only for media you are authorized to access and download.

## Project structure

```text
android/                 Native Android app and embedded Python packaging
ary_episode_watcher.py   Local API, catalogue, playback and download service
web/                     Responsive browser application
docs/                    Responsive project website for GitHub Pages
tests/                   Python unit tests
```

The app package identifier is `com.arywatcher.app`. Release assets and update information are listed on the [Releases page](https://github.com/rath0darya/Ary/releases).
