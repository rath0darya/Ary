# CineWave — standalone Android APK

This is a native Android app. Catalogue browsing, search, episode details, downloads, and playback use Android views and a native Media3 player. An embedded Python service supplies the existing catalogue and direct-stream APIs and FFmpegKit handles supported downloads. The app does not require Termux, a separately installed Python app, or a browser. Online catalogue and stream sources still require internet access.

## Build an APK without a PC

1. Open the repository on GitHub.
2. Select Actions → Build native CineWave Android APK → Run workflow.
3. Wait for the build to finish.
4. Open the completed workflow run and download the temporary cinewave-android-apk artifact.
5. Extract the ZIP and install app-debug.apk on an Android device. If Android asks, allow installation from that source. If you installed an earlier debug APK from a different build, uninstall that older copy first because GitHub Actions signs each debug build with a temporary key.

The workflow keeps the APK as a temporary GitHub Actions artifact for 7 days. It does not publish a GitHub Release or host a permanent APK download page.

## What is bundled

- Python 3.11 runtime via Chaquopy.
- Existing Python catalogue, stream inspection and local HTTP API.
- Catalogue seed data copied into app assets during the build.
- FFmpegKit native Android libraries for HLS downloads and FFprobe validation.
- A native Media3 ExoPlayer that plays HLS and MP4 streams from the local API.
- Native Android catalogue and episode screens with poster images, search, refresh, download progress and Android Downloads export.

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
