#!/usr/bin/env python3
"""Responsive, dependency-free interactive menu for ARY Episode Watcher on Termux."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

APP_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("ARYWEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARYWEB_PORT", "8787"))
BASE = f"http://{HOST}:{PORT}"
DOWNLOAD_DIR = Path(os.environ.get("ARY_DOWNLOAD_DIR", "/sdcard/Movies/ARY Episode Watcher"))

def request_json(path: str, timeout: int = 8):
    req = Request(BASE + path, headers={"Accept": "application/json", "User-Agent": "ARY-Termux-Menu/1.0"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def clear():
    if sys.stdout.isatty():
        os.system("clear")

def title():
    width = max(24, min(shutil.get_terminal_size((80, 24)).columns, 72))
    print("=" * width)
    print("ARY+ EPISODE WATCHER".center(width))
    print("Termux control panel".center(width))
    print("=" * width)

def status_text():
    try:
        data = request_json("/api/health", timeout=2)
        return f"ONLINE · {BASE} · FFmpeg {'ready' if data.get('ffmpeg') else 'missing'}"
    except (URLError, TimeoutError, OSError, ValueError):
        return f"OFFLINE · {BASE}"

def open_browser():
    url = f"http://127.0.0.1:{PORT}"
    opener = shutil.which("termux-open-url")
    if opener:
        subprocess.run([opener, url], check=False)
    else:
        print(f"Open this address in your Android browser: {url}")

def start_web():
    try:
        request_json("/api/health", timeout=2)
        print(f"The web interface is already running at {BASE}")
        input("\nPress Enter to return to the menu…")
        return
    except Exception:
        pass
    log_dir = Path.home() / ".ary-episode-watcher"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "aryweb.log"
    try:
        with log_path.open("ab") as log:
            subprocess.Popen(
                [sys.executable, str(APP_DIR / "ary_episode_watcher.py")],
                cwd=APP_DIR,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        print("Starting the web interface in the background…")
        for _ in range(12):
            time.sleep(0.5)
            try:
                request_json("/api/health", timeout=1)
                print(f"Web interface is ready: {BASE}")
                print("Choose option 2 to open it in your Android browser.")
                input("\nPress Enter to return to the menu…")
                return
            except Exception:
                continue
        print(f"Startup is taking longer than expected. Log: {log_path}")
    except Exception as exc:
        print(f"Could not start the web interface: {exc}")
    input("\nPress Enter to return to the menu…")

def refresh_catalogue():
    print("Refreshing the ARY catalogue. This can take some time…")
    try:
        data = request_json("/api/series?refresh=1", timeout=300)
        print(f"Catalogue refreshed: {len(data.get('series', []))} titles.")
    except Exception as exc:
        print(f"Could not refresh catalogue: {exc}")
        print("Start the web interface first, then try again.")
    input("\nPress Enter to return to the menu…")

def show_downloads():
    print(f"Download folder: {DOWNLOAD_DIR}")
    try:
        files = sorted((p for p in DOWNLOAD_DIR.iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm"}), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as exc:
        print(f"Folder unavailable: {exc}")
        files = []
    if not files:
        print("No downloaded video files found yet.")
    else:
        for item in files[:20]:
            try:
                size = item.stat().st_size
                size_text = f"{size / 1024**3:.2f} GB" if size >= 1024**3 else f"{size / 1024**2:.1f} MB"
                print(f"• {item.name}  ({size_text})")
            except OSError:
                print(f"• {item.name}")
    input("\nPress Enter to return to the menu…")

def main():
    while True:
        clear()
        title()
        print(status_text())
        print("\n  [1] Start web interface in background")
        print("  [2] Open web interface in browser")
        print("  [3] Check server and FFmpeg health")
        print("  [4] Refresh catalogue (server must be running)")
        print("  [5] Browse downloaded files")
        print("  [6] Show web address")
        print("  [0] Exit\n")
        choice = input("Select an option: ").strip()
        if choice == "1":
            start_web()
        elif choice == "2":
            open_browser()
            input("Press Enter to return to the menu…")
        elif choice == "3":
            try:
                data = request_json("/api/health")
                print(f"\nServer: online\nAddress: {BASE}\nFFmpeg: {'ready' if data.get('ffmpeg') else 'missing'}\nffprobe: {'ready' if data.get('ffprobe') else 'missing'}\nDownload folder: {data.get('download_dir', DOWNLOAD_DIR)}")
            except Exception as exc:
                print(f"\nServer unavailable: {exc}")
            input("\nPress Enter to return to the menu…")
        elif choice == "4":
            refresh_catalogue()
        elif choice == "5":
            show_downloads()
        elif choice == "6":
            print(f"\nOpen in browser: http://127.0.0.1:{PORT}")
            input("\nPress Enter to return to the menu…")
        elif choice in {"0", "q", "quit", "exit"}:
            print("Goodbye.")
            return
        else:
            print("Choose one of the listed options.")
            time.sleep(1)

if __name__ == "__main__":
    main()
