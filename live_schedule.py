#!/usr/bin/env python3
"""Schedule-aware ARY Digital live availability monitor (no media recording)."""
from __future__ import annotations

import json
import os
import re
import signal
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LIVE_PAGE = os.environ.get("ARY_LIVE_PAGE", "https://live.arydigital.tv/")
TZ = ZoneInfo("Asia/Kolkata")
START_TIME = clock_time(20, 30)
SCHEDULE_DAYS = {4, 5}  # Friday, Saturday
POLL_SECONDS = max(10, int(os.environ.get("ARY_LIVE_POLL_SECONDS", "30")))
ACTIVE_POLL_SECONDS = max(10, int(os.environ.get("ARY_LIVE_ACTIVE_POLL_SECONDS", "20")))
END_FAILURES = max(3, int(os.environ.get("ARY_LIVE_END_FAILURES", "5")))
STATE_DIR = Path(os.environ.get("ARY_LIVE_STATE_DIR", str(Path.home() / ".ary-episode-watcher")))
LOG_FILE = STATE_DIR / "live-monitor.jsonl"
USER_AGENT = os.environ.get(
    "ARY_LIVE_USER_AGENT",
    "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36",
)
STOP = False


def now() -> datetime:
    return datetime.now(TZ)


def log(event: str, **fields: Any) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    record = {"time": now().isoformat(timespec="seconds"), "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False)
    with LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    print(line, flush=True)


def fetch(url: str, timeout: int = 15) -> tuple[bytes, str, int]:
    request = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        "Referer": LIVE_PAGE,
        "Cache-Control": "no-cache",
    })
    with urlopen(request, timeout=timeout) as response:
        return response.read(3 * 1024 * 1024), response.geturl(), response.status


def candidate_playlists(page: bytes, final_url: str) -> list[str]:
    text = page.decode("utf-8", errors="ignore")
    text = text.replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&")
    found: list[str] = []
    patterns = [
        r'https?://[^\s"\'<>\\]+?\.m3u8(?:\?[^\s"\'<>\\]*)?',
        r'(?<![A-Za-z0-9])(?:/|\.\./|\./)[^\s"\'<>\\]+?\.m3u8(?:\?[^\s"\'<>\\]*)?',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.I):
            value = urljoin(final_url, match.rstrip("),;"))
            if urlparse(value).scheme in {"http", "https"} and value not in found:
                found.append(value)
    return found[:20]


def check_playlist(url: str) -> dict[str, Any]:
    try:
        data, final_url, status = fetch(url, timeout=12)
        text = data.decode("utf-8", errors="ignore")
        if status != 200 or "#EXTM3U" not in text[:4096]:
            return {"available": False, "reason": f"HTTP {status}; not an HLS playlist"}
        variants = re.findall(r"#EXT-X-STREAM-INF:[^\n]*\n([^\n#]+)", text)
        if variants:
            child = urljoin(final_url, variants[0].strip())
            child_data, _child_final, child_status = fetch(child, timeout=12)
            child_text = child_data.decode("utf-8", errors="ignore")
            ok = child_status == 200 and "#EXTM3U" in child_text[:4096]
            has_segments = bool(re.search(r"(?m)^(?!#)\S+", child_text))
            return {"available": ok and has_segments, "kind": "master",
                    "reason": "variant playlist reachable" if ok else "variant playlist unavailable"}
        has_segments = bool(re.search(r"(?m)^(?!#)\S+", text))
        ended = "#EXT-X-ENDLIST" in text
        return {"available": has_segments and not ended, "kind": "media",
                "reason": "live segments present" if has_segments and not ended else
                          ("playlist ended" if ended else "no segments")}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"[:240]}


def inspect_live_page() -> dict[str, Any]:
    try:
        page, final_url, status = fetch(LIVE_PAGE, timeout=20)
        urls = candidate_playlists(page, final_url)
        if not urls:
            return {"available": False, "page_status": status, "candidate_count": 0,
                    "reason": "Page loaded, but no HLS URL was discoverable in its HTML"}
        reasons = []
        for playlist in urls:
            result = check_playlist(playlist)
            if result.get("available"):
                # Never write signed stream URLs to logs.
                return {"available": True, "page_status": status, "candidate_count": len(urls),
                        "playlist_kind": result.get("kind", "hls"),
                        "reason": result.get("reason", "live playlist reachable")}
            reasons.append(str(result.get("reason", "unavailable")))
        return {"available": False, "page_status": status, "candidate_count": len(urls),
                "reason": "; ".join(reasons[:3]) or "No reachable HLS playlist"}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"[:240]}


def next_scheduled_start(after: datetime, last_checked_date=None) -> datetime:
    """Next schedule time; if started late today, begin monitoring immediately once."""
    base = after.astimezone(TZ)
    if base.weekday() in SCHEDULE_DAYS and base.time().replace(tzinfo=None) >= START_TIME:
        if last_checked_date != base.date():
            return base
    for offset in range(8):
        day = (base + timedelta(days=offset)).date()
        if day.weekday() not in SCHEDULE_DAYS:
            continue
        candidate = datetime.combine(day, START_TIME, TZ)
        if candidate >= base:
            return candidate
    raise RuntimeError("Unable to calculate next scheduled start")


def wait_until(target: datetime) -> None:
    while not STOP:
        remaining = (target - now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 15))


def monitor_broadcast() -> None:
    log("scheduled_window_open", page=LIVE_PAGE)
    consecutive_failures = 0
    announced = False
    while not STOP:
        result = inspect_live_page()
        if result["available"]:
            consecutive_failures = 0
            log("live_detected" if not announced else "live_still_available", **result)
            announced = True
            time.sleep(ACTIVE_POLL_SECONDS)
            continue
        if not announced:
            log("waiting_for_live", **result)
            time.sleep(POLL_SECONDS)
            continue
        consecutive_failures += 1
        log("live_check_failed", consecutive_failures=consecutive_failures, **result)
        if consecutive_failures >= END_FAILURES:
            log("broadcast_may_have_ended", consecutive_failures=consecutive_failures)
            return
        time.sleep(ACTIVE_POLL_SECONDS)


def run_scheduler() -> None:
    last_checked_date = None
    log("monitor_started", page=LIVE_PAGE, timezone="Asia/Kolkata",
        schedule=["Friday 20:30", "Saturday 20:30"], recording=False)
    while not STOP:
        target = next_scheduled_start(now(), last_checked_date)
        log("next_check_scheduled", scheduled_for=target.isoformat())
        wait_until(target)
        if STOP:
            break
        last_checked_date = target.date()
        monitor_broadcast()
        time.sleep(2)


def _stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True
    print("\nStopping live monitor…", flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    run_scheduler()
