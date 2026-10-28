# CineWave — standalone Android APK

This is a native Android app. Catalogue browsing, search, episode details, downloads, and playback use Android views and a native Media3 player. An embedded Python service supplies the existing catalogue and direct-stream APIs and FFmpegKit handles supported downloads. The app does not require Termux, a separately installed Python app, or a browser. Online catalogue and stream sources still require internet access.

## Download or build the APK

Download the current app from the [latest GitHub Release](https://github.com/rath0darya/Ary/releases/latest/download/CineWave.apk). To build it yourself, open the repository's Actions tab and run **Build native CineWave Android APK**. A successful build replaces the APK in the latest Release and also uploads a temporary `cinewave-android-apk` artifact for 7 days.

Install `CineWave.apk` on your Android device. If Android rejects an update because the debug signing key differs, uninstall the older debug build first. If Android asks, allow installation from that source.

## What is bundled

- Python 3.11 runtime via Chaquopy.
- Existing Python catalogue, stream inspection and local HTTP API.
- Catalogue seed data copied into app assets during the build.
- FFmpegKit native Android libraries for HLS downloads and FFprobe validation.
- A native Media3 ExoPlayer that plays HLS and MP4 streams from the local API.
- Native Android catalogue and episode screens with poster images, search, refresh, download progress and Android Downloads export.
- VOD episode discovery through the repo's ARY+ paginated catalogue and per-episode media endpoints. No episode-page redirect is used for playback.
- Optional ARY API key entry from the 🔑 controls. The key stays in the app's private runtime storage and is never packaged into the APK.

## Storage and privacy

The catalogue cache and download working files are stored in the app's private storage. Completed downloads are exported to Android's Downloads folder and appear in the system download notification. Uninstalling the app removes its private cache and working files.

Android 10 and newer save downloads without a storage prompt. Android 7 through 9 asks for storage permission when exporting a completed download.

## Development build

With Java 17, Android SDK 35, Gradle 8.13 and internet access installed:

    sh android/sync_sources.sh
    gradle --no-daemon -p android :app:assembleDebug

Output:

    android/app/build/outputs/apk/debug/app-debug.apk

This is a debug APK, not a Play Store release-signed APK. The workflow uploads it as a temporary artifact for 7 days and does not publish a GitHub Release.
