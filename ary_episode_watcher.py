#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"

ARY_BASE = os.environ.get("ARY_BASE_URL", "https://be.aryplus.tv").rstrip("/")
ARY_WEB = os.environ.get("ARY_WEB_URL", "https://aryplus.tv").rstrip("/")
DISCOVERY_URLS = [
    item.strip() for item in os.environ.get(
        "ARY_DISCOVERY_URLS",
        "https://aryplus.tv/browse/genre/684848223e08d31efd33fbcc"
    ).split(",") if item.strip()
]
CACHE_DIR = Path(os.environ.get(
    "ARY_CACHE_DIR",
    str(Path.home() / ".ary-episode-watcher"),
))
API_KEY_FILE = CACHE_DIR / "api-key.txt"
DOWNLOAD_DIR = Path(os.environ.get(
    "ARY_DOWNLOAD_DIR",
    "/sdcard/Movies/ARY Episode Watcher",
))
HOST = os.environ.get("ARYWEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARYWEB_PORT", "8787"))
USER_AGENT = os.environ.get(
    "ARY_USER_AGENT",
    "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36",
)

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
CACHE: dict[str, Any] = {}
CACHE_LOCK = threading.RLock()


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> bytes:
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        merged.update(headers)
    request = Request(url, headers=merged)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def http_text(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> str:
    return http_get(url, headers, timeout).decode("utf-8", "replace")


def extract_json_value(text: str, names: list[str]) -> str | None:
    for name in names:
        pattern = (
            r'["\']' + re.escape(name) +
            r'["\']\s*:\s*["\']([^"\']+)["\']'
        )
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return None


def get_api_key() -> str | None:
    env_key = os.environ.get("ARY_API_KEY", "").strip()
    if env_key:
        return env_key

    try:
        key = API_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass

    try:
        page = http_text(ARY_WEB, timeout=15)
    except Exception:
        return None

    key = extract_json_value(
        page,
        ["apiKey", "api_key", "x-api-key", "X-API-Key"],
    )
    if key:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            API_KEY_FILE.write_text(key + "\n", encoding="utf-8")
            try:
                os.chmod(API_KEY_FILE, 0o600)
            except OSError:
                pass
        except OSError:
            pass
    return key


def api_headers() -> dict[str, str]:
    headers = {
        "Referer": ARY_WEB + "/",
        "Origin": ARY_WEB,
        "Accept": "application/json, text/plain, */*",
    }
    key = get_api_key()
    if key:
        headers["x-api-key"] = key
        headers["X-API-Key"] = key
        headers["Authorization"] = "Bearer " + key
    return headers


def api_json(path: str, timeout: int = 25) -> Any:
    url = path if path.startswith("http") else ARY_BASE + path
    raw = http_get(url, api_headers(), timeout)
    return json.loads(raw.decode("utf-8", "replace"))


def normalise_episode(item: dict[str, Any]) -> dict[str, Any]:
    number = (
        item.get("episodeNumber")
        or item.get("episode_number")
        or item.get("number")
        or item.get("episode")
        or item.get("no")
    )
    title = (
        item.get("title")
        or item.get("name")
        or item.get("episodeTitle")
        or (f"Episode {number}" if number is not None else SERIES_NAME)
    )
    episode_id = (
        item.get("id")
        or item.get("_id")
        or item.get("episodeId")
        or item.get("episode_id")
    )
    return {
        "id": str(episode_id) if episode_id is not None else "",
        "number": number,
        "title": str(title),
        "date": item.get("date") or item.get("publishedAt") or item.get("createdAt"),
        "thumbnail": item.get("thumbnail") or item.get("image") or item.get("poster"),
        "raw": item,
    }


def _series_from_html(text: str, source_url: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    patterns = [
        r'href=["\\\']/title/([A-Za-z0-9]+)["\\\'][^>]*>(.*?)</a>',
        r'href=["\\\']/title/([A-Za-z0-9]+)["\\\']',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I | re.S):
            series_id = match.group(1)
            title = re.sub(r"<[^>]+>", " ", match.group(2)).strip() if match.lastindex and match.lastindex > 1 else ""
            title = re.sub(r"\\s+", " ", title)
            if series_id not in found:
                found[series_id] = {
                    "id": series_id,
                    "title": title or series_id,
                    "url": urljoin(source_url, "/title/" + series_id),
                }
    return list(found.values())


def discover_series(force: bool = False) -> list[dict[str, Any]]:
    with CACHE_LOCK:
        if not force and CACHE.get("series"):
            return CACHE["series"]

    found: dict[str, dict[str, Any]] = {}
    for source_url in DISCOVERY_URLS:
        try:
            html = http_text(source_url, timeout=25)
            for item in _series_from_html(html, source_url):
                found[item["id"]] = item
        except Exception as exc:
            print("[DISCOVERY]", source_url, "failed:", exc)

    if not found:
        raise RuntimeError("Unable to discover ARY series from the configured catalogue pages.")

    series = sorted(found.values(), key=lambda x: x["title"].lower())
    with CACHE_LOCK:
        CACHE["series"] = series
        CACHE["series_at"] = time.time()
    return series


def _series_key(series_id: str) -> str:
    return "episodes:" + series_id


def catalogue(series_id: str, force: bool = False) -> list[dict[str, Any]]:
    key = _series_key(series_id)
    with CACHE_LOCK:
        if not force and CACHE.get(key):
            return CACHE[key]

    episodes: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page in range(1, 501):
        data = api_json(
            f"/api/v2/cdn/pg/{quote(series_id, safe='')}?page={page}&limit=50"
        )
        raw_items = data.get("episode") if isinstance(data, dict) else data
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if not isinstance(raw_items, list) or not raw_items:
            break

        before = len(episodes)
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = normalise_episode(raw)
            if item["id"] and item["id"] not in seen:
                seen.add(item["id"])
                episodes.append(item)

        if len(episodes) == before:
            break

        has_next = data.get("hasNextPage") if isinstance(data, dict) else None
        total_pages = data.get("totalPages") if isinstance(data, dict) else None
        if has_next is False or (total_pages and page >= int(total_pages)):
            break

    episodes.sort(
        key=lambda x: (
            int(str(x["number"]).strip()) if str(x["number"]).strip().isdigit() else -1
        ),
        reverse=True,
    )
    with CACHE_LOCK:
        CACHE[key] = episodes
    return episodes

