"""Starts the existing ARY Python backend inside the Android app process."""
from __future__ import annotations

import os
from pathlib import Path


def start_server(runtime_dir: str) -> None:
    root = Path(runtime_dir).resolve()
    web_dir = root / "web"
    cache_dir = root / "data"
    download_dir = root / "downloads"
    web_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    os.environ["ARY_ANDROID_APP"] = "1"
    os.environ["ARY_APP_DIR"] = str(root)
    os.environ["ARY_WEB_DIR"] = str(web_dir)
    os.environ["ARY_CACHE_DIR"] = str(cache_dir)
    os.environ["ARY_DOWNLOAD_DIR"] = str(download_dir)
    os.environ["ARYWEB_HOST"] = "127.0.0.1"
    os.environ["ARYWEB_PORT"] = "8787"

    from ary_episode_watcher import main
    main()
