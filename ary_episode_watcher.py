#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import html
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
    item.strip()
    for item in os.environ.get(
        "ARY_DISCOVERY_URLS",
        ",".join([
            "https://aryplus.tv/browse/genre/684848223e08d31efd33fbcc",
            "https://aryplus.tv/browse/genre/66a1fcffba822788a3c11ed8",
            "https://aryplus.tv/browse/genre/64f373fa0b5df297f83569c1",
            "https://aryplus.tv/browse/genre/64f374c49476c891bba80a16",
            "https://aryplus.tv/browse/genre/677d1cb843f1091ceb256ab1",
            "https://aryplus.tv/browse/genre/64f374725813b7bff6cbd802",
            "https://aryplus.tv/browse/genre/684032e9f8d1dd20986123d5",
            "https://aryplus.tv/browse/genre/685034f9ed08c2bef38ffec0",
            "https://aryplus.tv/browse/genre/669a467156ded50194cf0df0",
        ]),
    ).split(",")
    if item.strip()
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
METADATA_JOB: dict[str, Any] = {"state":"idle","done":0,"total":0,"error":""}
METADATA_LOCK = threading.RLock()

# Persistent local catalogue. Normal requests read this instead of rediscovering ARY.
CATALOGUE_FILE = CACHE_DIR / "catalogue.json"
CACHE: dict[str, Any] = {}
CACHE_LOCK = threading.RLock()

def _load_catalogue_store() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CATALOGUE_FILE.exists():
        return
    try:
        data = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            CACHE.update(data)
            CACHE.setdefault("series", [])
            CACHE.setdefault("details", {})
            saved_episodes = CACHE.pop("episodes", {})
            if isinstance(saved_episodes, dict):
                for key, value in saved_episodes.items():
                    if str(key).startswith("episodes:"):
                        CACHE[key] = value
            print("[CATALOGUE] Loaded persistent catalogue", flush=True)
    except Exception as exc:
        print("[CATALOGUE] Load failed:", exc, flush=True)

def _save_catalogue_store() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CACHE_LOCK:
        data = {
            "schema_version": 1,
            "updated_at": time.time(),
            "series": CACHE.get("series", []),
            "details": CACHE.get("details", {}),
            "episodes": {k: v for k, v in CACHE.items() if k.startswith("episodes:")},
        }
    tmp = CATALOGUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CATALOGUE_FILE)


_load_catalogue_store()

def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, body: bytes, status: int = 200):
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def image_response(handler: BaseHTTPRequestHandler, target_url: str):
    parsed = urlparse(target_url)
    if parsed.scheme not in ("http", "https"):
        return json_response(handler, {"ok": False, "error": "Only HTTP(S) images are allowed."}, 400)
    try:
        request = Request(target_url, headers={
            "User-Agent": USER_AGENT,
            "Referer": ARY_WEB + "/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        })
        with urlopen(request, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            if not content_type.lower().startswith("image/"):
                return json_response(handler, {"ok": False, "error": "Target did not return an image."}, 415)
            data = response.read(12 * 1024 * 1024 + 1)
            if len(data) > 12 * 1024 * 1024:
                return json_response(handler, {"ok": False, "error": "Image is too large."}, 413)
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "public, max-age=3600")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(data)
    except Exception as exc:
        return json_response(handler, {"ok": False, "error": "Image proxy failed: " + str(exc)}, 502)


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> bytes:
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    request = Request(url, headers=merged)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def http_text(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> str:
    return http_get(url, headers, timeout).decode("utf-8", "replace")


def extract_json_value(text: str, names: list[str]) -> str | None:
    for name in names:
        pattern = r"""["']""" + re.escape(name) + r"""["']\s*:\s*["']([^"']+)["']"""
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
    key = extract_json_value(page, ["apiKey", "api_key", "x-api-key", "X-API-Key"])
    if key:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            API_KEY_FILE.write_text(key + "\n", encoding="utf-8")
            os.chmod(API_KEY_FILE, 0o600)
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
        or item.get("videoEpNumber")
        or item.get("number")
        or item.get("episode")
        or item.get("no")
    )
    title = (
        item.get("title")
        or item.get("name")
        or item.get("episodeTitle")
        or (f"Episode {number}" if number is not None else "Episode")
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
        "thumbnail": (
            item.get("thumbnail")
            or item.get("thumbnailUrl")
            or item.get("image")
            or item.get("imageUrl")
            or item.get("poster")
            or item.get("cover")
        ),
        "raw": item,
    }


def _series_from_html(text: str, source_url: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    pattern = r"""href=["']/?title/([A-Za-z0-9]+)["'][^>]*>(.*?)</a>"""
    for match in re.finditer(pattern, text, re.I | re.S):
        series_id, anchor = match.group(1), match.group(2)
        context = text[max(0, match.start() - 1200):min(len(text), match.end() + 2500)]
        is_live = bool(re.search(r"\bLive(?:\b|\s)", context, re.I))
        is_movie = bool(re.search(r"\bMovie(?:\b|\s)", context, re.I))
        is_series = bool(re.search(r"\bSeries(?:\b|\s|\d)", context, re.I))
        is_telefilm = bool(re.search(r"\bTelefilms?\b", context, re.I))
        if is_live or not (is_movie or is_series or is_telefilm):
            continue
        if is_telefilm and not is_series:
            content_type = "Telefilm"
        elif is_movie and not is_series:
            content_type = "Movie"
        else:
            content_type = "Series"

        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", anchor)).strip()
        title = re.sub(r"^\s*13\+\s*", "", title, flags=re.I)
        title = re.sub(r"\s*(?:Series|Movie|Live)\s*\d+.*$", "", title, flags=re.I).strip()

        image = None
        image_patterns = [
            r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
            r'background-image\s*:\s*url\((["\']?)([^)"\']+)\\1\)',
        ]
        local_context = text[max(0, match.start() - 1200):match.end()]
        for image_pattern in image_patterns:
            image_match = re.search(image_pattern, local_context, re.I | re.S)
            if image_match:
                image = image_match.group(2) if len(image_match.groups()) > 1 else image_match.group(1)
                image = urljoin(source_url, image)
                break

        found.setdefault(series_id, {
            "id": series_id,
            "title": title or series_id,
            "url": urljoin(source_url, "/title/" + series_id),
            "image": image,
            "content_type": content_type,
        })
    return list(found.values())


def _internal_links(text: str, source_url: str) -> tuple[set[str], set[str]]:
    """Return ARY genre/category pages and title pages linked by a catalogue page."""
    genre_links: set[str] = set()
    title_links: set[str] = set()
    for raw in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', text, re.I):
        url = urljoin(source_url, html.unescape(raw))
        parsed = urlparse(url)
        if parsed.netloc not in {"aryplus.tv", "www.aryplus.tv"}:
            continue
        path = parsed.path.rstrip("/")
        if re.match(r"^/browse/genre/[A-Za-z0-9]+$", path, re.I):
            genre_links.add(url)
        elif re.match(r"^/title/[A-Za-z0-9]+$", path, re.I):
            title_links.add(url)
    return genre_links, title_links


def _discovery_page_variants(url: str) -> list[str]:
    """Generate common public pagination forms used by ARY catalogue pages."""
    parsed = urlparse(url)
    base = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return [
        url + ("&" if parsed.query else "?") + "page=" + str(page)
        for page in range(2, 26)
    ] + [
        base + "?offset=" + str(offset)
        for offset in range(100, 2501, 100)
    ] + [
        base + "?skip=" + str(offset)
        for offset in range(100, 2501, 100)
    ]


def discover_series(force: bool = False) -> list[dict[str, Any]]:
    with CACHE_LOCK:
        if not force and CACHE.get("series"):
            return CACHE["series"]

    found: dict[str, dict[str, Any]] = {}
    queue: list[str] = list(dict.fromkeys(DISCOVERY_URLS))
    queued = set(queue)
    visited: set[str] = set()
    title_pages: list[str] = []

    # Do not assume the hand-written genre list is complete. ARY exposes
    # additional genre/category pages from its own catalogue pages.
    while queue and len(visited) < 300:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            html = http_text(url, timeout=25)
        except Exception as exc:
            print("[DISCOVERY]", url, "failed:", exc, flush=True)
            continue

        for item in _series_from_html(html, url):
            found[item["id"]] = item

        genre_links, title_links = _internal_links(html, url)
        for link in genre_links:
            if link not in visited and link not in queued:
                queue.append(link)
                queued.add(link)

        for link in title_links:
            if link not in title_pages and len(title_pages) < 1500:
                title_pages.append(link)

        # Probe public pagination only when it produces new title IDs. A
        # server that ignores the parameter simply yields duplicates.
        if re.search(r"/browse/genre/", url, re.I):
            for variant in _discovery_page_variants(url):
                if variant not in visited and variant not in queued and len(queue) < 400:
                    queue.append(variant)
                    queued.add(variant)

    # A second pass over title pages lets the catalogue grow from ARY's own
    # related-content links, without depending on search-engine indexing.
    for index, url in enumerate(title_pages, 1):
        if index > 1500:
            break
        try:
            html = http_text(url, timeout=20)
            for item in _series_from_html(html, url):
                found[item["id"]] = item
        except Exception as exc:
            print("[DISCOVERY-TITLE]", url, "failed:", exc, flush=True)

    if not found:
        raise RuntimeError("Unable to discover ARY catalogue content from configured catalogue pages.")

    result = sorted(found.values(), key=lambda x: x["title"].lower())
    with CACHE_LOCK:
        CACHE["series"] = result
    _save_catalogue_store()
    print("[DISCOVERY] pages=", len(visited), "titles=", len(result), flush=True)
    return result


def _html_meta(text: str, name: str) -> str | None:
    patterns = [
        r'<meta[^>]+(?:name|property)=["\\\']' + re.escape(name) + r'["\\\'][^>]+content=["\\\']([^"\\\']+)',
        r'<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+(?:name|property)=["\\\']' + re.escape(name) + r'["\\\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


def series_details(series_id: str, force: bool = False) -> dict[str, Any]:
    series_id = series_id.strip()
    with CACHE_LOCK:
        saved = CACHE.get("details", {}).get(series_id)
    if saved and not force:
        return saved
    base = next(
        (x for x in discover_series() if x["id"] == series_id),
        {"id": series_id, "title": series_id, "url": urljoin(ARY_WEB, "/title/" + series_id)},
    )
    try:
        page = http_text(base.get("url") or urljoin(ARY_WEB, "/title/" + series_id), timeout=20)
    except Exception:
        return {**base, "year": None, "genres": [], "description": "", "cast": []}

    def clean_meta(value: str | None) -> str:
        value = html.unescape(str(value or ""))
        value = re.sub(r"<[^>]+>", " ", value)
        value = re.sub(r"\\s+", " ", value).strip()
        if not value or any(token in value for token in ('"image":', '"trailer":', '"@type":', '":["')):
            return ""
        return value

    title = clean_meta(_html_meta(page, "og:title")) or base.get("title") or series_id
    description = clean_meta(_html_meta(page, "og:description")) or clean_meta(_html_meta(page, "description"))
    image = clean_meta(_html_meta(page, "og:image")) or base.get("image")
    if image and not str(image).startswith(("http://", "https://")):
        image = base.get("image")
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", page)
    year = int(year_match.group(1)) if year_match else None

    def json_array(label: str) -> list[str]:
        match = re.search(r'["\\]'+re.escape(label)+r'["\\]\s*:\s*\\?\[([^\\]]*)\\]?', page, re.I | re.S)
        if not match:
            return []
        return [html.unescape(x).strip() for x in re.findall(r'["\\]([^"\\]+)["\\]', match.group(1)) if x.strip()][:12]

    def nearby(label: str) -> list[str]:
        match = re.search(r"(?is)" + re.escape(label) + r"\s*[:\-]?\s*([^<]{0,300})", page)
        if not match:
            return []
        value = clean_meta(match.group(1))
        if not value:
            return []
        return [x.strip() for x in re.split(r"[,|•]", value) if x.strip()][:12]

    genres = json_array("genres") or nearby("Genres") or nearby("Genre")
    cast = json_array("cast") or json_array("actors") or nearby("Cast") or nearby("Starring") or nearby("Actors")

    # ARY distinguishes normal serials (Series), TV/reality programming
    # (Show), movies and telefilms on title pages. Keep that distinction in
    # the local catalogue instead of collapsing every episodic title into
    # "Series".
    type_context = page[:120000]
    detected_type = base.get("content_type") or "Series"
    if re.search(r"\b\d+\s+Episodes?\s+Show\b", type_context, re.I):
        detected_type = "Show"
    elif re.search(r"\b\d+\s+Episodes?\s+Telefilm\b", type_context, re.I):
        detected_type = "Telefilm"
    elif re.search(r"\b\d+\s+Episodes?\s+Series\b", type_context, re.I):
        detected_type = "Series"
    elif re.search(r"\bTelefilms?\b", " ".join(genres), re.I):
        detected_type = "Telefilm"
    elif re.search(r"\bTV Shows?\b|\bReality\b|\bGame Show\b", " ".join(genres), re.I):
        detected_type = "Show"

    clean_title = re.sub(r"\s*\|.*$", "", title).strip()
    result = {
        **base,
        "title": clean_title or base.get("title") or series_id,
        "image": image,
        "backdrop": image,
        "year": year,
        "genres": genres,
        "description": description,
        "cast": cast,
        "content_type": detected_type,
    }
    with CACHE_LOCK:
        CACHE.setdefault("details", {})[series_id] = result
    _save_catalogue_store()
    return result


def _metadata_worker():
    global METADATA_JOB
    with METADATA_LOCK:
        METADATA_JOB = {"state":"running","done":0,"total":len(CACHE.get("series",[])),"error":""}
    try:
        items = list(CACHE.get("series", []))
        for index, item in enumerate(items, 1):
            try:
                detail = series_details(item["id"], force=True)
                merged = {**item, **detail}
                with CACHE_LOCK:
                    for pos, existing in enumerate(CACHE.get("series", [])):
                        if existing.get("id") == item["id"]:
                            CACHE["series"][pos] = merged
                            break
            except Exception as exc:
                print("[METADATA]", item.get("id"), "failed:", exc, flush=True)
            with METADATA_LOCK:
                METADATA_JOB["done"] = index
            if index % 10 == 0:
                _save_catalogue_store()
        _save_catalogue_store()
        with METADATA_LOCK:
            METADATA_JOB["state"] = "completed"
    except Exception as exc:
        with METADATA_LOCK:
            METADATA_JOB["state"] = "error"
            METADATA_JOB["error"] = str(exc)

def start_metadata_update() -> dict[str, Any]:
    with METADATA_LOCK:
        if METADATA_JOB.get("state") == "running":
            return dict(METADATA_JOB)
    threading.Thread(target=_metadata_worker, daemon=True).start()
    return {"state":"running","done":0,"total":len(CACHE.get("series",[])),"error":""}

def _series_key(series_id: str) -> str:
    return "episodes:" + series_id


def catalogue(series_id: str, force: bool = False) -> list[dict[str, Any]]:
    series_id = series_id.strip()
    if not series_id:
        raise ValueError("series is required")
    key = _series_key(series_id)
    with CACHE_LOCK:
        if not force and CACHE.get(key):
            return CACHE[key]

    base = next(
        (item for item in discover_series() if item["id"] == series_id),
        {"id": series_id, "url": urljoin(ARY_WEB, "/title/" + series_id)},
    )
    page_url = base.get("url") or urljoin(ARY_WEB, "/title/" + series_id)
    html = http_text(page_url, {"Referer": ARY_WEB + "/"}, timeout=30)

    marker = '"episodes":['
    start = html.find(marker)
    escaped = False
    if start < 0:
        marker = '\\"episodes\\":['
        start = html.find(marker)
        escaped = start >= 0
    if start < 0:
        # Movies/telefilms and some special titles expose one direct videoSource
        # instead of an "episodes" array. Represent that source as a single
        # playable local catalogue item so the same player/download path works.
        sources = re.findall(
            r'["\\\']videoSource["\\\']\\s*:\\s*["\\\'](https?://[^"\\\']+)["\\\']',
            html,
            re.I,
        )
        if not sources:
            sources = re.findall(r'https?://[^"\\\']+?\.m3u8(?:\?[^"\\\']*)?', html, re.I)
        if sources:
            unique_source = next((x for x in sources if ".m3u8" in x.lower()), sources[0])
            with CACHE_LOCK:
                CACHE[key] = [{
                    "id": series_id + ":movie",
                    "number": 1,
                    "title": base.get("title") or series_id,
                    "date": None,
                    "thumbnail": base.get("image"),
                    "stream": unique_source,
                    "description": "",
                    "next_episode_id": None,
                    "raw": {"content_type": base.get("content_type", "Movie")},
                }]
            _save_catalogue_store()
            return CACHE[key]
        raise RuntimeError("No episode or direct video source was found on the ARY title page.")

    array_start = html.find("[", start)
    catalogue_text = html[array_start:]
    if escaped:
        catalogue_text = catalogue_text.replace('\\"', '"')
    try:
        raw_items, _ = json.JSONDecoder().raw_decode(catalogue_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unable to parse the ARY episode catalogue.") from exc
    if not isinstance(raw_items, list):
        raise RuntimeError("ARY returned an invalid episode catalogue.")

    episodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict) or str(raw.get("seriesId") or "") != series_id:
            continue
        item = normalise_episode({
            "_id": raw.get("_id"),
            "seriesId": raw.get("seriesId"),
            "videoEpNumber": raw.get("videoEpNumber"),
            "videoSource": raw.get("videoSource"),
            "title": raw.get("title"),
            "description": raw.get("description"),
            "nextEpId": raw.get("nextEpId"),
            "thumbnail": raw.get("thumbnail"),
            "thumbnailUrl": raw.get("thumbnailUrl"),
            "image": raw.get("image"),
            "imageUrl": raw.get("imageUrl"),
        })
        item["stream"] = raw.get("videoSource")
        item["description"] = raw.get("description") or ""
        item["next_episode_id"] = raw.get("nextEpId")
        if item["id"] and item["id"] not in seen:
            seen.add(item["id"])
            episodes.append(item)

    episodes.sort(
        key=lambda x: (
            int(str(x["number"]).strip())
            if str(x["number"]).strip().isdigit()
            else -1
        ),
        reverse=True,
    )
    with CACHE_LOCK:
        CACHE[key] = episodes
    _save_catalogue_store()
    return episodes


def find_episode(series_id: str, episode_id: str) -> dict[str, Any]:
    for episode in catalogue(series_id):
        if episode["id"] == episode_id:
            return episode
    raise KeyError("Episode does not belong to the selected series.")


def parse_hls_master(text: str, base_url: str) -> dict[str, Any]:
    variants: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = {}
            attrs = line.split(":", 1)[1]
            for key, value in re.findall(r'([A-Z0-9-]+)=(".*?"|[^,]+)', attrs):
                value = value.strip('"')
                pending[key.lower().replace("-", "_")] = value
            resolution = pending.get("resolution")
            if resolution and "x" in resolution:
                width, height = resolution.split("x", 1)
                try:
                    pending["width"] = int(width)
                    pending["height"] = int(height)
                except ValueError:
                    pass
            for field in ("bandwidth", "average_bandwidth"):
                if field in pending:
                    try:
                        pending[field] = int(pending[field])
                    except (TypeError, ValueError):
                        pass
        elif line and not line.startswith("#") and pending is not None:
            pending["uri"] = urljoin(base_url, line)
            variants.append(pending)
            pending = None
    return {"variants": variants, "base_url": base_url}


def choose_best_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [
        item for item in variants
        if item.get("uri") and item.get("height")
    ]
    if not usable:
        return None
    return max(
        usable,
        key=lambda item: (
            item.get("height") or 0,
            item.get("width") or 0,
            item.get("bandwidth") or 0,
            item.get("average_bandwidth") or 0,
        ),
    )


def _find_video_source(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return value
        return None
    if isinstance(value, dict):
        for key in ("videoSource", "video_source", "url", "uri", "src", "source"):
            found = _find_video_source(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _find_video_source(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_video_source(child)
            if found:
                return found
    return None


def inspect_episode_stream(episode_id: str, source_hint: str | None = None) -> dict[str, Any]:
    source = source_hint
    if not source:
        try:
            data = api_json("/api/cdn/ep/" + quote(episode_id, safe=""))
            source = _find_video_source(data)
        except Exception:
            source = None
    if not source:
        raise RuntimeError("No video stream was returned for this episode.")

    playlist = http_text(source, {"Referer": ARY_WEB + "/"}, timeout=30)
    parsed = parse_hls_master(playlist, source)
    best = choose_best_variant(parsed["variants"])
    return {
        "source": source,
        "variants": parsed["variants"],
        "best": best,
    }


def safe_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "-", str(value))
    value = re.sub(r"[^A-Za-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:180] or "episode"

def _job_update(job_id: str, **values: Any):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)


def _download_worker(job_id: str, stream_url: str, output: Path, quality: str, resolution: str):
    _job_update(job_id, state="downloading", quality=quality, resolution=resolution)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-headers", "Referer: " + ARY_WEB + "/\r\nOrigin: " + ARY_WEB + "\r\n",
        "-i", stream_url,
        "-c", "copy",
        "-movflags", "+faststart",
        str(output),
    ]
    try:
        started = time.time()
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        while True:
            line = process.stderr.readline() if process.stderr else ""
            if not line and process.poll() is not None:
                break
            if output.exists():
                size = output.stat().st_size
                elapsed = max(0.1, time.time() - started)
                _job_update(
                    job_id,
                    size=size,
                    speed=f"{size / elapsed / 1024 / 1024:.1f} MB/s",
                )
        code = process.wait()
        if code != 0:
            raise RuntimeError((process.stderr.read() if process.stderr else "").strip() or f"ffmpeg exited with {code}")
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("FFmpeg completed without creating a file.")

        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            check = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "json", str(output)],
                capture_output=True, text=True, check=False,
            )
            if check.returncode != 0:
                raise RuntimeError("ffprobe could not validate the downloaded file.")
        size = output.stat().st_size
        _job_update(job_id, state="completed", percent=100, size=size, filename=str(output))
    except Exception as exc:
        _job_update(job_id, state="error", error=str(exc), filename=str(output))


def start_download(
    series_id: str,
    episode_id: str,
    episode_number: Any = None,
    episode_title: str = "",
    series_name: str = "Series",
) -> dict[str, Any]:
    episode = find_episode(series_id, episode_id)
    info = inspect_episode_stream(episode_id, episode.get("stream"))
    best = info.get("best")
    if not best:
        raise RuntimeError("No usable HLS variant was advertised.")

    quality = f'{best.get("height", "?")}p'
    resolution = f'{best.get("width", "?")}x{best.get("height", "?")}'
    series_label = series_name or "Series"
    ep_label = episode_number if episode_number is not None else episode.get("number")
    title = episode_title or episode.get("title") or f"Episode {ep_label or episode_id}"
    filename = safe_filename(f"{series_label} - Episode {ep_label or 'Unknown'} - {title}") + ".mp4"
    output = DOWNLOAD_DIR / filename
    job_id = uuid.uuid4().hex

    job = {
        "id": job_id,
        "series_id": series_id,
        "series_name": series_label,
        "episode_id": episode_id,
        "episode_number": ep_label,
        "episode_title": title,
        "state": "queued",
        "percent": 0,
        "size": 0,
        "quality": quality,
        "resolution": resolution,
        "filename": str(output),
        "error": "",
        "created": time.time(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(
        target=_download_worker,
        args=(job_id, best["uri"], output, quality, resolution),
        daemon=True,
    ).start()
    return job


class Handler(BaseHTTPRequestHandler):
    server_version = "ARYWatcher/1.0"

    def log_message(self, fmt: str, *args):
        print(f"[HTTP] {self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                file = WEB_DIR / "index.html"
                if not file.exists():
                    return json_response(self, {"ok": False, "error": "Web UI not found."}, 500)
                return html_response(self, file.read_bytes())

            if path == "/api/image":
                target = query.get("url", [""])[0].strip()
                if not target:
                    return json_response(self, {"ok": False, "error": "url is required"}, 400)
                return image_response(self, target)

            if path == "/api/health":
                return json_response(self, {
                    "ok": True,
                    "host": HOST,
                    "port": PORT,
                    "ffmpeg": shutil.which("ffmpeg") is not None,
                    "ffprobe": shutil.which("ffprobe") is not None,
                    "download_dir": str(DOWNLOAD_DIR),
                })

            if path == "/api/series":
                return json_response(self, {
                    "ok": True,
                    "series": discover_series(query.get("refresh", ["0"])[0] == "1"),
                })

            if path == "/api/catalogue/metadata":
                if query.get("start", ["0"])[0] == "1":
                    return json_response(self, {"ok": True, "job": start_metadata_update()})
                with METADATA_LOCK:
                    return json_response(self, {"ok": True, "job": dict(METADATA_JOB)})

            if path == "/api/series/detail":
                sid = query.get("series", [""])[0].strip()
                if not sid:
                    return json_response(self, {"ok": False, "error": "series is required"}, 400)
                return json_response(self, {"ok": True, "series": series_details(sid)})

            if path == "/api/episodes":
                sid = query.get("series", [""])[0].strip()
                if not sid:
                    return json_response(self, {"ok": False, "error": "series is required"}, 400)
                return json_response(self, {
                    "ok": True,
                    "series_id": sid,
                    "episodes": catalogue(sid, query.get("refresh", ["0"])[0] == "1"),
                })

            if path == "/api/stream-info":
                sid = query.get("series", [""])[0].strip()
                eid = query.get("episode", [""])[0].strip()
                if not sid or not eid:
                    return json_response(self, {"ok": False, "error": "series and episode are required"}, 400)
                episode = find_episode(sid, eid)
                return json_response(self, {
                    "ok": True,
                    "episode": episode,
                    "stream": inspect_episode_stream(eid, episode.get("stream")),
                })

            if path == "/api/download":
                sid = query.get("series", [""])[0].strip()
                eid = query.get("episode", [""])[0].strip()
                if not sid or not eid:
                    return json_response(self, {"ok": False, "error": "series and episode are required"}, 400)
                ep = find_episode(sid, eid)
                series_name = next(
                    (item["title"] for item in discover_series() if item["id"] == sid),
                    sid,
                )
                number = query.get("number", [ep.get("number")])[0]
                title = query.get("title", [ep.get("title", "")])[0]
                job = start_download(sid, eid, number, title, series_name)
                return json_response(self, {"ok": True, "job": job})

            if path.startswith("/api/download/file/"):
                job_id = path.rsplit("/", 1)[-1]
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if not job or job.get("state") != "completed":
                    return json_response(self, {"ok": False, "error": "Download is not completed yet."}, 409)
                output = Path(job["filename"]).resolve()
                if not output.is_file() or not output.is_relative_to(DOWNLOAD_DIR.resolve()):
                    return json_response(self, {"ok": False, "error": "Download file is unavailable."}, 404)
                data = output.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", 'attachment; filename="' + output.name.replace('"', "") + '"')
                self.end_headers()
                self.wfile.write(data)
                return

            if path.startswith("/api/download/"):
                job_id = path.rsplit("/", 1)[-1]
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if not job:
                    return json_response(self, {"ok": False, "error": "Job not found"}, 404)
                return json_response(self, {"ok": True, "job": job})

            return json_response(self, {"ok": False, "error": "Not found"}, 404)
        except Exception as exc:
            return json_response(self, {"ok": False, "error": str(exc)}, 500)


def main():
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ARY Watcher listening on http://{HOST}:{PORT}", flush=True)
    print(f"Project: {APP_DIR}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ARY Watcher...", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
