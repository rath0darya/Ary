#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import html
import re
import shutil
import shlex
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from live_check import inspect_live_page

APP_DIR = Path(os.environ.get("ARY_APP_DIR", str(Path(__file__).resolve().parent)))
WEB_DIR = Path(os.environ.get("ARY_WEB_DIR", str(APP_DIR / "web")))

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
            "https://aryplus.tv/",
            "https://aryplus.tv/browse",
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
            data = response.read(12 * 1024 * 1024 + 1)
            if not content_type.lower().startswith("image/"):
                if data.startswith(b"\x89PNG\r\n\x1a\n"):
                    content_type = "image/png"
                elif data.startswith(b"\xff\xd8\xff"):
                    content_type = "image/jpeg"
                elif data.startswith((b"GIF87a", b"GIF89a")):
                    content_type = "image/gif"
                elif len(data) > 12 and data[4:12] == b"ftypavif":
                    content_type = "image/avif"
                elif data.startswith((b"RIFF",)) and b"WEBP" in data[:32]:
                    content_type = "image/webp"
                elif data.lstrip().startswith(b"<svg"):
                    content_type = "image/svg+xml"
                else:
                    return json_response(handler, {"ok": False, "error": "Target did not return an image."}, 415)
            if len(data) > 12 * 1024 * 1024:
                return json_response(handler, {"ok": False, "error": "Image is too large."}, 413)
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "public, max-age=3600")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(data)
    except BrokenPipeError:
        # Browser cancelled the image request (common with lazy-loaded posters).
        return None
    except Exception as exc:
        try:
            return json_response(handler, {"ok": False, "error": "Image proxy failed: " + str(exc)}, 502)
        except BrokenPipeError:
            return None


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


def _media_url(value: Any, base_url: str = ARY_WEB) -> str | None:
    """Extract and absolutise an image/media URL from ARY's varied shapes."""
    if isinstance(value, str):
        value = html.unescape(value).strip().replace("\\/", "/")
        if value.startswith(("http://", "https://")):
            return value
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("/"):
            return urljoin(base_url + "/", value.lstrip("/"))
        return urljoin(base_url + "/", value)
    if isinstance(value, dict):
        for key in ("url", "src", "image", "imageUrl", "thumbnail", "thumbnailUrl",
                    "poster", "posterUrl", "cover", "coverUrl", "thumbnailImage", "thumbnail_image",
                    "episodeImage", "episode_image", "episodeThumbnail", "episode_thumbnail",
                    "videoImage", "video_image", "videoThumbnail", "video_thumbnail", "path"):
            result = _media_url(value.get(key), base_url)
            if result:
                return result
    if isinstance(value, list):
        for entry in value:
            result = _media_url(entry, base_url)
            if result:
                return result
    return None


def _balanced_json_value(text: str, start: int) -> str | None:
    """Return one balanced JSON array/object starting at start."""
    if start >= len(text) or text[start] not in "[{":
        return None
    opening = text[start]
    closing = "]" if opening == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _json_candidates(text: str) -> list[Any]:
    """Find JSON payloads embedded in HTML, including escaped Next.js data."""
    if not text:
        return []
    decoded = html.unescape(text)
    variants = [
        text,
        decoded,
        decoded.replace('\\/"', '/"').replace('\\/', '/').replace('\\\"', '"'),
        decoded.replace('\\/', '/').replace('\\\"', '"'),
    ]
    candidates: list[Any] = []
    seen: set[str] = set()

    for blob in variants:
        for match in re.finditer(r"<script[^>]*>(.*?)</script>", blob, re.I | re.S):
            script = match.group(1).strip()
            if script:
                variants.append(script) if len(variants) < 20 else None
        for start in [m.start() for m in re.finditer(r"[\\[{]", blob)]:
            value = _balanced_json_value(blob, start)
            if not value or len(value) > 5_000_000 or value in seen:
                continue
            seen.add(value)
            try:
                candidates.append(json.loads(value))
            except (ValueError, TypeError):
                continue
    return candidates


def _looks_like_episode(item: dict[str, Any]) -> bool:
    keys = {str(k).lower() for k in item}
    return bool(keys & {
        "videoepnumber", "episodenumber", "episode_number", "episodeid",
        "episode_id", "videoid", "video_id", "videoepid", "nextepid",
        "videosource", "video_source",
        "episodetitle", "videotitle", "videotitle", "videotitletext", "seasonnumber",
        "episodeno", "episodenumber", "videonumber", "videopageno", "epno",
        "videourl", "video_url", "streamurl", "stream_url",
    })


def _collect_episode_dicts(value: Any, series_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if _looks_like_episode(node):
                sid = str(node.get("seriesId") or node.get("series_id") or "")
                if not sid or sid == series_id:
                    found.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return found


def _collect_title_dicts(value: Any, source_url: str) -> list[dict[str, Any]]:
    """Find ARY title records embedded in catalogue JSON/Next data."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ident = node.get("id") or node.get("_id") or node.get("seriesId") or node.get("series_id")
            title = node.get("title") or node.get("name")
            if ident and title:
                sid = str(ident)
                # ARY content IDs are alphanumeric; require a title-like record
                # to avoid treating arbitrary API objects as catalogue cards.
                if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", sid) and len(str(title).strip()) >= 2:
                    key = sid
                    if key not in seen:
                        seen.add(key)
                        image = _media_url(
                            node.get("image") or node.get("imageUrl") or
                            node.get("thumbnail") or node.get("thumbnailUrl") or
                            node.get("poster") or node.get("posterUrl") or
                            node.get("cover") or node.get("coverUrl")
                        )
                        raw_type = str(
                            node.get("content_type") or node.get("contentType") or
                            node.get("type") or node.get("category") or ""
                        ).lower()
                        if "telefilm" in raw_type:
                            ctype = "Telefilm"
                        elif "movie" in raw_type:
                            ctype = "Movie"
                        elif "show" in raw_type:
                            ctype = "Show"
                        else:
                            ctype = "Series"
                        found.append({
                            "id": sid,
                            "title": str(title).strip(),
                            "url": urljoin(source_url, "/title/" + sid),
                            "image": image,
                            "content_type": ctype,
                            "catalogue_genres": [],
                        })
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return found


def _extract_title_catalogue(text: str, source_url: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for payload in _json_candidates(text):
        for item in _collect_title_dicts(payload, source_url):
            found[item["id"]] = item
    return list(found.values())


def _extract_episode_catalogue(text: str, series_id: str) -> list[dict[str, Any]]:
    """Extract ARY episodes without depending on one exact HTML/JSON layout."""
    blobs = [
        text,
        html.unescape(text).replace('\\/', '/').replace('\\\"', '"'),
    ]
    raw: list[dict[str, Any]] = []

    # First use balanced extraction around common episode-array keys.
    for blob in blobs:
        for match in re.finditer(r"""["\\'](?:episodes|episodeList|episode_list|videos)["\\']\s*:\s*""", blob, re.I):
            start = match.end()
            while start < len(blob) and blob[start].isspace():
                start += 1
            value = _balanced_json_value(blob, start)
            if not value:
                continue
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                continue
            raw.extend(_collect_episode_dicts(parsed, series_id))

    # Then inspect embedded JSON payloads for layouts where the array is nested
    # under another property or the key is minified/renamed.
    for payload in _json_candidates(text):
        raw.extend(_collect_episode_dicts(payload, series_id))

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        eid = str(
            item.get("id") or item.get("_id") or item.get("episodeId") or
            item.get("episode_id") or item.get("videoId") or ""
        )
        fingerprint = eid or json.dumps(item, sort_keys=True, default=str)
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(item)
    return unique


def normalise_episode(item: dict[str, Any]) -> dict[str, Any]:
    number = (
        item.get("episodeNumber")
        or item.get("episode_number")
        or item.get("videoEpNumber")
        or item.get("episodeNo")
        or item.get("epNo")
        or item.get("videoNumber")
        or item.get("number")
        or item.get("episode")
        or item.get("no")
    )
    title = (
        item.get("title")
        or item.get("name")
        or item.get("episodeTitle")
        or item.get("videoTitle")
        or item.get("video_title")
        or item.get("name")
        or (f"Episode {number}" if number is not None else "Episode")
    )
    episode_id = (
        item.get("id")
        or item.get("_id")
        or item.get("episodeId")
        or item.get("episode_id")
        or item.get("videoId")
        or item.get("video_id")
    )
    return {
        "id": str(episode_id) if episode_id is not None else "",
        "number": number,
        "title": str(title),
        "date": item.get("date") or item.get("publishedAt") or item.get("createdAt"),
        "thumbnail": _media_url(
            item.get("episodeImage")
            or item.get("episode_image")
            or item.get("videoImage")
            or item.get("video_image")
            or item.get("image")
            or item.get("imageUrl")
            or item.get("image_url")
            or item.get("poster")
            or item.get("posterUrl")
            or item.get("poster_url")
            or item.get("cover")
            or item.get("cover_image")
            or item.get("backdrop")
            or item.get("still")
            or item.get("stillUrl")
            or item.get("still_url")
            or item.get("thumbnailImage")
            or item.get("thumbnail_image")
            or item.get("thumbnail")
            or item.get("thumbnailUrl")
            or item.get("thumbnail_url")
            or item.get("episodeThumbnail")
            or item.get("episode_thumbnail")
            or item.get("videoThumbnail")
            or item.get("video_thumbnail")
        ),
        "raw": item,
    }


def _series_from_html(text: str, source_url: str, source_hint: str = "") -> list[dict[str, Any]]:
    """Parse ARY catalogue cards without leaking type information between cards."""
    found: dict[str, dict[str, Any]] = {}
    pattern = r"""href=["']/?title/([A-Za-z0-9]+)["'][^>]*>(.*?)</a>"""
    matches = list(re.finditer(pattern, text, re.I | re.S))
    hint = re.sub(r"\s+", " ", html.unescape(source_hint or "")).strip()
    hint_lower = hint.lower()

    def visible(value: str) -> str:
        value = html.unescape(value)
        value = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def marker_type(value: str) -> str | None:
        """Read only a card-local Movie/Series/Live marker."""
        value = visible(value)
        # ARY's card text is commonly: "Title Movie", "Title Series49 Ep",
        # or "Title Series22 Ep". Keep the marker tied to this card.
        match = re.search(
            r"\b(Live|Movie|Series)(?:\s*\d+)?(?:\s*Ep(?:isode)?\s*\d+)?\b",
            value,
            re.I,
        )
        if not match:
            return None
        return match.group(1).lower()

    for index, match in enumerate(matches):
        series_id, anchor = match.group(1), match.group(2)
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)

        # The ARY marker can be outside the <a> element (for example a sibling
        # span containing "Series49 Ep"). Restrict inspection to the current
        # card's text, ending at the next title link, so the next card cannot
        # change this item's type.
        card_block = text[match.start():next_start]
        marker = marker_type(anchor) or marker_type(card_block[:1200])

        if hint_lower in {"telefilms", "telefilm"}:
            content_type = "Telefilm"
        elif hint_lower in {"tv shows", "shows", "show"}:
            content_type = "Show"
        elif marker == "live":
            content_type = "Live"
        elif marker == "movie":
            content_type = "Movie"
        elif marker == "series":
            content_type = "Series"
        else:
            # A title without a visible type marker is still useful for
            # discovery, but leave classification for title-page metadata.
            content_type = None

        if content_type == "Live":
            continue
        # Some ARY cards omit the type marker in the anchor HTML. Keep the
        # title anyway; title-page metadata will classify it later.
        if not content_type:
            content_type = "Series"

        title = visible(anchor)
        title = re.sub(r"^\s*13\+\s*", "", title, flags=re.I)
        title = re.sub(r"\s*(?:Series|Movie|Live)\s*\d+.*$", "", title, flags=re.I).strip()
        title = re.sub(r"\s+(?:Series|Movie|Live)\s*$", "", title, flags=re.I).strip()

        image = None
        local_context = text[max(0, match.start() - 1200):match.end()]
        for image_pattern in [
            r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
            r'background-image\s*:\s*url\((["\']?)([^)"\']+)\\1\)',
        ]:
            image_match = re.search(image_pattern, local_context, re.I | re.S)
            if image_match:
                image = image_match.group(2) if len(image_match.groups()) > 1 else image_match.group(1)
                image = urljoin(source_url, image)
                break

        item = found.setdefault(series_id, {
            "id": series_id,
            "title": title or series_id,
            "url": urljoin(source_url, "/title/" + series_id),
            "image": image,
            "content_type": content_type,
            "catalogue_genres": [],
        })
        item["content_type"] = content_type
        if image and not item.get("image"):
            item["image"] = image
        if hint and hint_lower not in {"tv shows", "shows", "all"}:
            genres = item.setdefault("catalogue_genres", [])
            if hint not in genres:
                genres.append(hint)
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



def _catalogue_heading(text: str) -> str:
    """Extract the visible ARY catalogue heading."""
    match = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.I | re.S)
    if not match:
        return ""
    value = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
    return re.sub(r"\s+", " ", value).strip()

def discover_series(force: bool = False) -> list[dict[str, Any]]:
    # This installation is intentionally focused on Dar-E-Nijaat rather than
    # the full ARY+ catalogue. Official ARY Digital pages are the source of truth.
    if os.environ.get("ARY_SERIES_MODE", "dar-e-nijaat").strip().lower() == "dar-e-nijaat":
        item = {
            "id": "dar-e-nijaat",
            "title": "Dar-E-Nijaat",
            "url": "https://arydigital.tv/drama/dar-e-nijaat/",
            "image": "https://backend.arydigital.tv/uploads/Dar_e_Nijat_Poster_jpg_d5142f9c36.jpeg",
            "content_type": "Series",
            "catalogue_genres": ["Drama", "Romance"],
            "genres": ["Drama", "Romance"],
            "description": "A drama series. Browse episodes and continue watching from your saved position.",
        }
        with CACHE_LOCK:
            CACHE["series"] = [item]
        _save_catalogue_store()
        return [item]

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
    while queue and len(visited) < 750:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            html = http_text(url, timeout=25)
        except Exception as exc:
            print("[DISCOVERY]", url, "failed:", exc, flush=True)
            continue

        page_hint = _catalogue_heading(html)
        for item in _series_from_html(html, url, page_hint):
            found[item["id"]] = item
        # Modern ARY catalogue pages often hydrate cards from embedded JSON
        # rather than rendering every title as a normal <a> element.
        for item in _extract_title_catalogue(html, url):
            if page_hint:
                hint_lower = page_hint.lower()
                if hint_lower in {"telefilms", "telefilm"}:
                    item["content_type"] = "Telefilm"
                elif hint_lower in {"tv shows", "shows", "show"}:
                    item["content_type"] = "Show"
                item.setdefault("catalogue_genres", [])
                if page_hint not in item["catalogue_genres"] and hint_lower not in {"all", "browse"}:
                    item["catalogue_genres"].append(page_hint)
            previous = found.get(item["id"])
            if previous:
                if not previous.get("image") and item.get("image"):
                    previous["image"] = item["image"]
                if previous.get("content_type") == "Series" and item.get("content_type") != "Series":
                    previous["content_type"] = item["content_type"]
            else:
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
            for item in _series_from_html(html, url, ""):
                found[item["id"]] = item
        except Exception as exc:
            print("[DISCOVERY-TITLE]", url, "failed:", exc, flush=True)

    if not found:
        raise RuntimeError("Unable to discover ARY catalogue content from configured catalogue pages.")

    # ARY sometimes exposes its generic portal-shell title as if it were a
    # movie/show. These are not actual catalogue entries and can repeat dozens
    # of times across sports/entertainment genre pages.
    result_items = []
    for item in found.values():
        title = re.sub(r"\\s+", " ", str(item.get("title") or "")).strip()
        if title.casefold() in {
            "ary plus - a video streaming portal",
            "ary+ - a video streaming portal",
            "ary plus",
        }:
            continue
        if not title:
            continue
        result_items.append(item)
    result = sorted(result_items, key=lambda x: x["title"].lower())
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

    # ARY exposes human-readable metadata on the title page, e.g.
    # "Genres Drama, Family" or "Genres Telefilm, Telefilms".
    visible = html.unescape(re.sub(r"<[^>]+>", " ", page))
    visible = re.sub(r"\s+", " ", visible).strip()
    genre_text = ""
    genre_match = re.search(
        r"\bGenres?\s+(.{1,240}?)(?=\s+(?:Age Rating|Audio|Cast|Starring|You Might Also Like|Trending Now)\b|$)",
        visible,
        re.I,
    )
    if genre_match:
        genre_text = genre_match.group(1).strip(" .:-")
    visible_genres = [x.strip() for x in re.split(r"[,|•]", genre_text) if x.strip() and len(x.strip()) < 80]
    genres = visible_genres or json_array("genres") or nearby("Genres") or nearby("Genre")
    catalogue_genres = list(base.get("catalogue_genres") or [])
    for g in catalogue_genres:
        if g and g not in genres:
            genres.append(g)
    cast = json_array("cast") or json_array("actors") or nearby("Cast") or nearby("Starring") or nearby("Actors")

    # ARY distinguishes normal serials (Series), TV/reality programming
    # (Show), movies and telefilms on title pages. Keep that distinction in
    # the local catalogue instead of collapsing every episodic title into
    # "Series".
    type_context = visible[:120000]
    detected_type = base.get("content_type") or "Series"
    header_type = re.search(
        r"\b(?:\d{4}\s+)?(?:\d+\s+Episodes?\s+)?(Telefilm|Series|Show|Movie)\b",
        type_context,
        re.I,
    )
    if header_type:
        raw_type = header_type.group(1).lower()
        detected_type = {
            "telefilm": "Telefilm",
            "series": "Series",
            "show": "Show",
            "movie": "Movie",
        }[raw_type]
    # ARY can label a telefilm asset as Movie while its genres/category say
    # Telefilm. Preserve the more specific catalogue type.
    if re.search(r"\bTelefilms?\b", " ".join(genres), re.I):
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


def _extract_video_sources(text: str) -> list[str]:
    """Extract direct ARY video/HLS URLs from HTML or embedded page JSON."""
    if not text:
        return []
    blobs = [text]
    normalized = html.unescape(
        text.replace('\\\\\"', '"').replace('\\\\/', '/').replace('\\/', '/')
    )
    blobs.append(normalized)
    found: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = value.strip().replace('\\/', '/')
        if value.startswith(("http://", "https://")) and value not in seen:
            seen.add(value)
            found.append(value)

    for blob in blobs:
        for match in re.finditer(
            r'''["\\'](?:videoSource|video_source|videoUrl|video_url|streamUrl|stream_url|sourceUrl|source_url)["\\']\s*:\s*["\\'](https?://[^"\\']+)["\\']''',
            blob, re.I,
        ):
            add(match.group(1))
        for match in re.finditer(
            r'''https?://[^"\\'<>\s]+(?:\.m3u8(?:\?[^"\\'<>\s]*)?|\.mp4(?:\?[^"\\'<>\s]*)?)''',
            blob, re.I,
        ):
            add(match.group(0))
    return found


def _episode_api_candidates(text: str, series_id: str) -> list[str]:
    """Extract likely episode/video JSON endpoints exposed by the ARY page."""
    candidates: list[str] = []
    seen: set[str] = set()
    blobs = [html.unescape(text).replace("\\/", "/").replace("\\\"", '"')]
    # Absolute API URLs.
    for blob in blobs:
        for m in re.finditer(r"""https?://[^"\\'<>\s]+""", blob, re.I):
            url = m.group(0).rstrip("\\'\")],;")
            if re.search(r"/api/|episode|video|series", url, re.I) and url not in seen:
                seen.add(url); candidates.append(url)
        # Relative API routes embedded in JSON/JS.
        for m in re.finditer(r"""["\\']((?:/)?api/[^"\\']+)["\\']""", blob, re.I):
            path = m.group(1)
            path = re.sub(r"\$\{(?:series|seriesId|id)\}", series_id, path)
            path = re.sub(r"\{(?:series|seriesId|id)\}", series_id, path)
            if re.search(r"episode|video|series|title|content", path, re.I):
                full = urljoin(ARY_BASE + "/", path.lstrip("/"))
                if full not in seen:
                    seen.add(full); candidates.append(full)
    return candidates[:40]


def catalogue(series_id: str, force: bool = False) -> list[dict[str, Any]]:
    series_id = series_id.strip()
    if not series_id:
        raise ValueError("series is required")
    key = _series_key(series_id)

    # Keep verified episode IDs/HLS sources as the offline baseline, but refresh
    # the official ARY episode guide so newly published episodes are discovered.
    dar_e_nijaat_aliases = {
        "dar-e-nijaat",
        "dar_e_nijaat",
        "dar-e-nijaat-series",
        "6a57868b5bf57c474cc00a50",
    }
    if series_id.lower() in dar_e_nijaat_aliases:
        source_file = APP_DIR / "dar-e-nijaat-all-m3u8.json"
        try:
            source_data = json.loads(source_file.read_text(encoding="utf-8"))
            saved_items = source_data.get("episodes", [])
            if not isinstance(saved_items, list) or not saved_items:
                raise ValueError("canonical episode list is empty")
            episodes: list[dict[str, Any]] = []
            for item in saved_items:
                number = int(item["episode"])
                episode_id = str(item["id"])
                stream_url = str(item["m3u8"])
                if not episode_id or not stream_url.startswith(("https://", "http://")):
                    raise ValueError(f"invalid canonical data for episode {number}")
                episodes.append({
                    "id": episode_id,
                    "api_id": episode_id,
                    "number": number,
                    "title": f"Dar-E-Nijaat Episode {number}",
                    "date": None,
                    "thumbnail": "https://backend.arydigital.tv/uploads/Dar_e_Nijat_Poster_jpg_d5142f9c36.jpeg",
                    "stream": stream_url,
                    "official_url": f"https://arydigital.tv/drama/dar-e-nijaat/episode-{number}/",
                    "official_only": False,
                    "description": "Verified saved HLS source; new episodes are checked against the official ARY episode guide.",
                    "next_episode_id": None,
                    "raw": {"source": "dar-e-nijaat-all-m3u8.json", "episode": number},
                })
            episodes.sort(key=lambda episode: episode["number"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to load canonical Dar-E-Nijaat episode data: {exc}") from exc

        # Cache the live check for five minutes to avoid hammering ARY. A forced
        # refresh bypasses this window; if ARY is temporarily unavailable, keep
        # the known-good episodes and try again on the next refresh.
        with CACHE_LOCK:
            cached = CACHE.get(key)
            checked_at = float(CACHE.get("dar_e_nijaat_checked_at", 0) or 0)
            if not force and cached and time.time() - checked_at < 300:
                return cached

        known_numbers = {
            int(item["number"]) for item in episodes
            if str(item.get("number", "")).isdigit()
        }
        # Preserve previously discovered episodes across refreshes/restarts,
        # even if ARY's page is temporarily unavailable during this check.
        with CACHE_LOCK:
            previous_episodes = list(CACHE.get(key, []) or [])
        canonical_ids = {str(item["id"]) for item in episodes}
        for previous in previous_episodes:
            previous_id = str(previous.get("id") or "")
            try:
                previous_number = int(previous.get("number"))
            except (TypeError, ValueError):
                continue
            if previous_id and previous_id not in canonical_ids and previous_number not in known_numbers:
                episodes.append(previous)
                canonical_ids.add(previous_id)
                known_numbers.add(previous_number)
        # IMPORTANT: use the same ARY+ paginated episode API used by the
        # original web version. The public ARY Digital page can lag behind VOD
        # publication and often renders only the first 20 episode links.
        api_added = 0
        api_seen_ids = {str(item.get("id") or "") for item in episodes}
        api_seen_numbers = {
            int(item["number"]) for item in episodes
            if str(item.get("number", "")).isdigit()
        }
        for page_number in range(1, 101):
            try:
                payload = api_json(
                    f"/api/v2/cdn/pg/{quote(str(source_data['seriesId']), safe='')}?page={page_number}&limit=50",
                    timeout=20,
                )
            except Exception as exc:
                print(f"[DAR-E-NIJAAT] ARY+ episode API page {page_number} failed: {exc}", flush=True)
                break
            raw_page = payload.get("episode") if isinstance(payload, dict) else payload
            if isinstance(raw_page, dict):
                raw_page = [raw_page]
            if not isinstance(raw_page, list) or not raw_page:
                break
            page_added = 0
            for raw in raw_page:
                if not isinstance(raw, dict):
                    continue
                normalized = normalise_episode(raw)
                episode_id = str(normalized.get("id") or "")
                raw_number = normalized.get("number")
                try:
                    episode_number = int(raw_number)
                except (TypeError, ValueError):
                    continue
                # Only trust actual ARY records with a real content ID and a
                # positive episode number; never fabricate IDs from numbering.
                if not re.fullmatch(r"[A-Fa-f0-9]{24}", episode_id) or episode_number < 1:
                    continue
                if episode_id in api_seen_ids or episode_number in api_seen_numbers:
                    continue
                stream_url = (
                    _media_url(raw.get("videoSource"), "https://arydigital.tv")
                    or _media_url(raw.get("video_source"), "https://arydigital.tv")
                    or _media_url(raw.get("streamUrl"), "https://arydigital.tv")
                    or _media_url(raw.get("stream_url"), "https://arydigital.tv")
                    or _media_url(raw.get("videoUrl"), "https://arydigital.tv")
                    or _media_url(raw.get("video_url"), "https://arydigital.tv")
                    or _media_url(raw.get("source"), "https://arydigital.tv")
                )
                episodes.append({
                    "id": episode_id,
                    "api_id": episode_id,
                    "number": episode_number,
                    "title": normalized.get("title") or f"Dar-E-Nijaat Episode {episode_number}",
                    "date": normalized.get("date"),
                    "thumbnail": normalized.get("thumbnail") or "https://backend.arydigital.tv/uploads/Dar_e_Nijat_Poster_jpg_d5142f9c36.jpeg",
                    "stream": stream_url,
                    "official_url": f"https://arydigital.tv/drama/dar-e-nijaat/episode-{episode_number}/",
                    "official_only": False,
                    "description": "Discovered from the live ARY+ episode API.",
                    "next_episode_id": raw.get("nextEpId") or raw.get("nextEpisodeId"),
                    "raw": raw,
                })
                api_seen_ids.add(episode_id)
                api_seen_numbers.add(episode_number)
                page_added += 1
                api_added += 1
            if page_added == 0:
                break
            has_next = payload.get("hasNextPage") if isinstance(payload, dict) else None
            total_pages = payload.get("totalPages") if isinstance(payload, dict) else None
            if has_next is False or (total_pages and page_number >= int(total_pages)):
                break

        if api_added:
            print(f"[DAR-E-NIJAAT] ARY+ API discovered {api_added} new episode(s)", flush=True)

        series_page_url = "https://arydigital.tv/drama/dar-e-nijaat/"
        candidate_numbers: set[int] = set()
        try:
            series_page = http_text(series_page_url, {"Referer": "https://arydigital.tv/"}, timeout=20)
            for match in re.finditer(r"(?:episode[-/](\d+))", html.unescape(series_page), re.I):
                candidate_numbers.add(int(match.group(1)))
        except Exception as exc:
            print("[DAR-E-NIJAAT] Official episode guide refresh failed:", exc, flush=True)

        # The guide can lag behind a fresh upload. Probe the next three official
        # episode URLs as well; only accept a candidate when its page contains a
        # real ARY episode record with a genuine content ID.
        next_number = max(known_numbers or {0}) + 1
        candidate_numbers.update(range(next_number, next_number + 4))
        additions: list[dict[str, Any]] = []
        seen_ids = {str(item["id"]) for item in episodes}
        for number in sorted(candidate_numbers):
            if number in known_numbers:
                continue
            episode_url = f"https://arydigital.tv/drama/dar-e-nijaat/episode-{number}/"
            try:
                page = http_text(episode_url, {"Referer": series_page_url}, timeout=15)
            except Exception:
                continue

            raw_items = _extract_episode_catalogue(page, source_data["seriesId"])
            if not raw_items:
                for endpoint in _episode_api_candidates(page, source_data["seriesId"]):
                    try:
                        payload = api_json(endpoint, timeout=12)
                        raw_items.extend(_collect_episode_dicts(payload, source_data["seriesId"]))
                        if raw_items:
                            break
                    except Exception:
                        continue

            for raw in raw_items:
                normalized = normalise_episode(raw)
                raw_number = normalized.get("number")
                try:
                    episode_number = int(raw_number) if raw_number is not None else number
                except (TypeError, ValueError):
                    episode_number = number
                episode_id = str(normalized.get("id") or "")
                # Never invent IDs or streams from the episode number alone.
                if episode_number != number or not re.fullmatch(r"[A-Fa-f0-9]{24}", episode_id):
                    continue
                if episode_id in seen_ids:
                    continue
                stream_url = (
                    _media_url(raw.get("videoSource"), "https://arydigital.tv")
                    or _media_url(raw.get("video_source"), "https://arydigital.tv")
                    or _media_url(raw.get("streamUrl"), "https://arydigital.tv")
                    or _media_url(raw.get("stream_url"), "https://arydigital.tv")
                    or _media_url(raw.get("videoUrl"), "https://arydigital.tv")
                    or _media_url(raw.get("video_url"), "https://arydigital.tv")
                    or _media_url(raw.get("source"), "https://arydigital.tv")
                )
                additions.append({
                    "id": episode_id,
                    "api_id": episode_id,
                    "number": episode_number,
                    "title": f"Dar-E-Nijaat Episode {episode_number}",
                    "date": normalized.get("date"),
                    "thumbnail": normalized.get("thumbnail") or "https://backend.arydigital.tv/uploads/Dar_e_Nijat_Poster_jpg_d5142f9c36.jpeg",
                    "stream": stream_url,
                    "official_url": episode_url,
                    "official_only": not bool(stream_url),
                    "description": "Discovered from the official ARY episode page.",
                    "next_episode_id": None,
                    "raw": raw,
                })
                seen_ids.add(episode_id)
                known_numbers.add(episode_number)
                break

        episodes.extend(additions)
        episodes.sort(key=lambda episode: int(episode.get("number") or 0))
        with CACHE_LOCK:
            CACHE[key] = episodes
            CACHE["dar_e_nijaat_checked_at"] = time.time()
        _save_catalogue_store()
        print(
            f"[DAR-E-NIJAAT] Catalogue has {len(episodes)} episodes "
            f"({len(additions)} newly discovered)",
            flush=True,
        )
        return episodes

    with CACHE_LOCK:
        if not force and CACHE.get(key):
            return CACHE[key]

    base = next(
        (item for item in discover_series() if item["id"] == series_id),
        {"id": series_id, "title": series_id, "url": urljoin(ARY_WEB, "/title/" + series_id)},
    )
    page_url = base.get("url") or urljoin(ARY_WEB, "/title/" + series_id)
    page = http_text(page_url, {"Referer": ARY_WEB + "/"}, timeout=30)

    raw_items = _extract_episode_catalogue(page, series_id)

    # Some ARY title pages are only an app shell; their episode catalogue
    # is fetched from a JSON endpoint referenced by the page. Try those
    # endpoints before concluding that the title has no episodes.
    if not raw_items:
        for endpoint in _episode_api_candidates(page, series_id):
            try:
                payload = api_json(endpoint, timeout=20)
                raw_items.extend(_collect_episode_dicts(payload, series_id))
                if raw_items:
                    break
            except Exception as exc:
                print("[EPISODES-API]", series_id, endpoint, "failed:", exc, flush=True)

    # Movies/telefilms and some specials expose one direct video source instead
    # of an episode list.
    if not raw_items:
        sources = _extract_video_sources(page)
        if sources:
            unique_source = next((x for x in sources if ".m3u8" in x.lower()), sources[0])
            with CACHE_LOCK:
                CACHE[key] = [{
                    "id": series_id + ":movie",
                    "number": 1,
                    "title": base.get("title") or series_id,
                    "date": None,
                    "thumbnail": _media_url(base.get("image")),
                    "stream": unique_source,
                    "description": "",
                    "next_episode_id": None,
                    "raw": {"content_type": base.get("content_type", "Movie")},
                }]
            _save_catalogue_store()
            return CACHE[key]

        raise RuntimeError("Unable to parse the ARY episode catalogue or find a direct video source.")

    episodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = normalise_episode(raw)
        # ARY has used both seriesId and nested parent objects. Accept an item
        # when its parent ID is absent; the title page itself is already scoped.
        item["stream"] = (
            _media_url(raw.get("videoSource"), ARY_WEB)
            or _media_url(raw.get("video_source"), ARY_WEB)
            or _media_url(raw.get("stream"), ARY_WEB)
            or _media_url(raw.get("streamUrl"), ARY_WEB)
            or _media_url(raw.get("stream_url"), ARY_WEB)
            or _media_url(raw.get("videoUrl"), ARY_WEB)
            or _media_url(raw.get("video_url"), ARY_WEB)
            or _media_url(raw.get("source"), ARY_WEB)
        )
        item["description"] = raw.get("description") or raw.get("episodeDescription") or ""
        item["next_episode_id"] = raw.get("nextEpId") or raw.get("nextEpisodeId") or raw.get("next_episode_id")
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
    if not episodes:
        raise RuntimeError("ARY episode data was found, but no usable episode records were extracted.")

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
    """Inspect an already available direct source; do not extract protected media."""
    source = source_hint
    if not source:
        try:
            data = api_json("/api/cdn/ep/" + quote(episode_id, safe=""))
            source = _find_video_source(data)
        except Exception:
            source = None
    if not source:
        raise RuntimeError(
            "No accessible direct video source is available for in-app playback or download. "
            "The episode webpage is not itself a video file."
        )

    if ".mp4" in source.lower().split("?", 1)[0]:
        return {
            "source": source,
            "variants": [],
            "best": {"uri": source, "width": None, "height": None, "bandwidth": None},
            "media_type": "video/mp4",
        }

    playlist = http_text(source, {"Referer": ARY_WEB + "/"}, timeout=30)
    parsed = parse_hls_master(playlist, source)
    best = choose_best_variant(parsed["variants"])
    if not best and ".m3u8" in source.lower():
        best = {"uri": source, "width": None, "height": None, "bandwidth": None}
    return {
        "source": source,
        "variants": parsed["variants"],
        "best": best,
        "media_type": "application/vnd.apple.mpegurl",
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
    _job_update(job_id, state="downloading", quality=quality, resolution=resolution, percent=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats",
        "-headers", "Referer: " + ARY_WEB + "/\r\nOrigin: " + ARY_WEB + "\r\n",
        "-i", stream_url,
        "-c", "copy",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        str(output),
    ]
    try:
        started = time.time()
        if os.environ.get("ARY_ANDROID_APP") == "1":
            # FFmpegKit is packaged as a native Android dependency in the standalone APK.
            from com.arthenica.ffmpegkit import FFmpegKit, FFprobeKit, ReturnCode

            _job_update(job_id, percent=5, speed="Starting native FFmpeg…")
            android_command = command[1:]
            if "-progress" in android_command:
                progress_at = android_command.index("-progress")
                del android_command[progress_at:progress_at + 2]
            command_text = shlex.join(android_command)
            session = FFmpegKit.execute(command_text)
            code = session.getReturnCode()
            if not ReturnCode.isSuccess(code):
                details = session.getFailStackTrace() or session.getOutput() or "FFmpegKit failed."
                raise RuntimeError(str(details))
            _job_update(job_id, percent=95, speed="Validating file…")
            probe_command = shlex.join([
                "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(output),
            ])
            probe = FFprobeKit.execute(probe_command)
            if not ReturnCode.isSuccess(probe.getReturnCode()):
                raise RuntimeError("FFprobe could not validate the downloaded file.")
        else:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            # FFmpeg progress output is machine-readable and independent of terminal width.
            if process.stdout:
                for line in process.stdout:
                    line = line.strip()
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key == "total_size":
                        try:
                            size = max(0, int(value))
                            elapsed = max(0.1, time.time() - started)
                            _job_update(job_id, size=size, speed=f"{size / elapsed / 1024 / 1024:.1f} MB/s")
                        except ValueError:
                            pass
                    elif key == "speed" and value not in {"N/A", "0x"}:
                        _job_update(job_id, speed=value)
                    elif key == "progress" and value == "end":
                        _job_update(job_id, percent=99)
            code = process.wait()
            error_text = process.stderr.read().strip() if process.stderr else ""
            if code != 0:
                raise RuntimeError(error_text or f"ffmpeg exited with {code}")
            ffprobe = shutil.which("ffprobe")
            if ffprobe:
                check = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height", "-of", "json", str(output)],
                    capture_output=True, text=True, check=False,
                )
                if check.returncode != 0:
                    raise RuntimeError("ffprobe could not validate the downloaded file.")
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("FFmpeg completed without creating a file.")
        size = output.stat().st_size
        _job_update(job_id, state="completed", percent=100, size=size, speed="", filename=str(output))
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
    info = inspect_episode_stream(episode.get("api_id") or episode_id, episode.get("stream"))
    best = info.get("best")
    if not best or not best.get("uri"):
        raise RuntimeError("No directly downloadable, non-DRM video source is available.")

    quality = f'{best.get("height") or "source"}p'
    resolution = f'{best.get("width") or "?"}x{best.get("height") or "?"}'
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


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    """Allow quick restarts and avoid waiting for client threads on shutdown."""
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class Handler(BaseHTTPRequestHandler):
    server_version = "ARYWatcher/1.0"

    def log_message(self, fmt: str, *args):
        print(f"[HTTP] {self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/sw.js":
                file = WEB_DIR / "sw.js"
                if not file.exists():
                    return json_response(self, {"ok": False, "error": "Service worker not found."}, 404)
                body = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

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

            if path == "/api/live/check":
                result = inspect_live_page()
                return json_response(self, {"ok": True, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), **result})

            if path == "/api/health":
                return json_response(self, {
                    "ok": True,
                    "host": HOST,
                    "port": PORT,
                    "ffmpeg": shutil.which("ffmpeg") is not None or os.environ.get("ARY_ANDROID_APP") == "1",
                    "ffprobe": shutil.which("ffprobe") is not None or os.environ.get("ARY_ANDROID_APP") == "1",
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
                return json_response(self, {
                    "ok": True,
                    "series": series_details(
                        sid,
                        force=query.get("refresh", ["0"])[0] == "1",
                    ),
                })

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
                    "stream": inspect_episode_stream(episode.get("api_id") or eid, episode.get("stream")),
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
                size = output.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", 'attachment; filename="' + output.name.replace('"', "") + '"')
                self.end_headers()
                with output.open("rb") as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return

            if path.startswith("/api/download/"):
                job_id = path.rsplit("/", 1)[-1]
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if not job:
                    return json_response(self, {"ok": False, "error": "Job not found"}, 404)
                return json_response(self, {"ok": True, "job": job})

            return json_response(self, {"ok": False, "error": "Not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The browser cancelled or closed the request; there is no client
            # left to receive an error response.
            return
        except Exception as exc:
            print(f"[HTTP-ERROR] {path}: {type(exc).__name__}: {exc}", flush=True)
            try:
                return json_response(self, {"ok": False, "error": str(exc)}, 500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return


def main():
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    server = ReusableThreadingHTTPServer((HOST, PORT), Handler)
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
