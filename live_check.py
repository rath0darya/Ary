#!/usr/bin/env python3
"""On-demand ARY Digital live availability check. No scheduler and no recording."""
from __future__ import annotations

import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

LIVE_PAGE = "https://live.arydigital.tv/"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
    "Chrome/140.0 Mobile Safari/537.36"
)


def _fetch(url: str, timeout: int = 15) -> tuple[bytes, str, int]:
    request = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        "Referer": LIVE_PAGE,
        "Cache-Control": "no-cache",
    })
    with urlopen(request, timeout=timeout) as response:
        return response.read(3 * 1024 * 1024), response.geturl(), response.status


def _candidate_playlists(page: bytes, final_url: str) -> list[str]:
    source = page.decode("utf-8", errors="ignore")
    source = source.replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&")
    patterns = [
        r'https?://[^\s"\'<>\\]+?\.m3u8(?:\?[^\s"\'<>\\]*)?',
        r'(?<![A-Za-z0-9])(?:/|\.\./|\./)[^\s"\'<>\\]+?\.m3u8(?:\?[^\s"\'<>\\]*)?',
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, source, flags=re.I):
            candidate = urljoin(final_url, match.rstrip("),;"))
            if urlparse(candidate).scheme in {"http", "https"} and candidate not in found:
                found.append(candidate)
    return found[:20]


def _check_playlist(url: str) -> dict[str, Any]:
    try:
        data, final_url, status = _fetch(url, timeout=12)
        source = data.decode("utf-8", errors="ignore")
        if status != 200 or "#EXTM3U" not in source[:4096]:
            return {"available": False, "reason": f"HTTP {status}; not an HLS playlist"}
        variants = re.findall(r"#EXT-X-STREAM-INF:[^\n]*\n([^\n#]+)", source)
        if variants:
            child = urljoin(final_url, variants[0].strip())
            child_data, _child_url, child_status = _fetch(child, timeout=12)
            child_text = child_data.decode("utf-8", errors="ignore")
            valid = child_status == 200 and "#EXTM3U" in child_text[:4096]
            has_segments = bool(re.search(r"(?m)^(?!#)\S+", child_text))
            return {
                "available": valid and has_segments,
                "reason": "HLS variant playlist is reachable" if valid and has_segments
                          else "HLS variant playlist is not currently playable",
            }
        has_segments = bool(re.search(r"(?m)^(?!#)\S+", source))
        ended = "#EXT-X-ENDLIST" in source
        return {
            "available": has_segments and not ended,
            "reason": "Live HLS segments are present" if has_segments and not ended
                      else ("Playlist has ended" if ended else "No media segments found"),
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"[:220]}


def inspect_live_page() -> dict[str, Any]:
    """Check only when explicitly called; never schedules follow-up checks."""
    try:
        page, final_url, status = _fetch(LIVE_PAGE, timeout=20)
        urls = _candidate_playlists(page, final_url)
        if not urls:
            return {
                "available": False,
                "page_status": status,
                "candidate_count": 0,
                "reason": "Live page loaded, but its HTML did not expose an HLS playlist. The player may load it dynamically.",
                "page_url": LIVE_PAGE,
            }
        reasons = []
        for playlist in urls:
            result = _check_playlist(playlist)
            if result.get("available"):
                # Do not return or log potentially signed stream URLs.
                return {
                    "available": True,
                    "page_status": status,
                    "candidate_count": len(urls),
                    "reason": result.get("reason", "HLS playlist reachable"),
                    "page_url": LIVE_PAGE,
                }
            reasons.append(str(result.get("reason", "Unavailable")))
        return {
            "available": False,
            "page_status": status,
            "candidate_count": len(urls),
            "reason": "; ".join(reasons[:3]) or "No reachable HLS playlist",
            "page_url": LIVE_PAGE,
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {
            "available": False,
            "candidate_count": 0,
            "reason": f"{type(exc).__name__}: {exc}"[:220],
            "page_url": LIVE_PAGE,
        }
