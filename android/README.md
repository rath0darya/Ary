# ARY Episode Watcher — standalone Android APK

This Android app packages the CINE•WAVE web interface, Python backend, catalogue seed data, and FFmpegKit native runtime inside one APK. It does not require Termux, a separately installed Python app, a browser, or a hosted copy of this website. Online catalogue and stream sources still require internet access.

## Build an APK without a PC

1. Open the repository on GitHub.
2. Select Actions → Build standalone Android APK → Run workflow.
3. Wait for the build to finish.
4. Open the completed workflow run and download the temporary ary-episode-watcher-debug-apk artifact.
5. Extract the ZIP and install app-debug.apk on an Android device. If Android asks, allow installation from that source.

The workflow keeps the APK as a temporary GitHub Actions artifact for 7 days. It does not publish a GitHub Release or host a permanent APK download page.

## What is bundled

- Python 3.11 runtime via Chaquopy.
- Existing Python catalogue, stream inspection and local HTTP API.
- Existing web/index.html, service worker and catalogue seed file copied into app assets during the build.
- FFmpegKit native Android libraries for HLS downloads and FFprobe validation.
- An Android WebView that opens the local Python server at 127.0.0.1:8787.

## Storage and privacy

The catalogue cache and download working files are stored in the app's private storage. A completed download can be exported to Android's Downloads folder from the in-app download link. Uninstalling the app removes its private cache and working files.

## Development build

With Java 17, Android SDK 35, Gradle 8.13 and internet access installed:

    sh android/sync_sources.sh
    gradle --no-daemon -p android :app:assembleDebug

Output:

    android/app/build/outputs/apk/debug/app-debug.apk

This is a debug APK, not a Play Store release-signed APK.
