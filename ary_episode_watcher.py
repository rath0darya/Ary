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


def html_response(handler: BaseHTTPRequestHandler, body: bytes, status: int = 200):
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


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
        pattern = r"""["']""" + re.escape(name) + r"""["']\\s*:\\s*["']([^"']+)["']"""
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
        "thumbnail": item.get("thumbnail") or item.get("image") or item.get("poster"),
        "raw": item,
    }


def _series_from_html(text: str, source_url: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    pattern = r"""href=["']/?title/([A-Za-z0-9]+)["'][^>]*>(.*?)</a>"""
    for match in re.finditer(pattern, text, re.I | re.S):
        series_id, anchor = match.group(1), match.group(2)
        context = text[max(0, match.start() - 700):min(len(text), match.end() + 1200)]
        if not re.search(r'\bSeries\b', context, re.I):
            continue
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", anchor)).strip()
        title = re.sub(r"\s*(?:Series|Movie|Live)\s*\d+.*$", "", title, flags=re.I).strip()
        found.setdefault(series_id, {
            "id": series_id,
            "title": title or series_id,
            "url": urljoin(source_url, "/title/" + series_id),
        })
    return list(found.values())


def discover_series(force: bool = False) -> list[dict[str, Any]]:
    with CACHE_LOCK:
        if not force and CACHE.get("series"):
            return CACHE["series"]
    found: dict[str, dict[str, Any]] = {}
    for url in DISCOVERY_URLS:
        try:
            html = http_text(url, timeout=25)
            for item in _series_from_html(html, url):
                found[item["id"]] = item
        except Exception as exc:
            print("[DISCOVERY]", url, "failed:", exc, flush=True)
    if not found:
        raise RuntimeError("Unable to discover ARY series from configured catalogue pages.")
    result = sorted(found.values(), key=lambda x: x["title"].lower())
    with CACHE_LOCK:
        CACHE["series"] = result
    return result


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
            int(str(x["number"]).strip())
            if str(x["number"]).strip().isdigit()
            else -1
        ),
        reverse=True,
    )
    with CACHE_LOCK:
        CACHE[key] = episodes
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
    variants.sort(
        key=lambda x: (
            x.get("height") or 0,
            x.get("width") or 0,
            x.get("bandwidth") or 0,
            x.get("average_bandwidth") or 0,
        ),
        reverse=True,
    )
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


def inspect_episode_stream(episode_id: str) -> dict[str, Any]:
    data = api_json("/api/cdn/ep/" + quote(episode_id, safe=""))
    source = _find_video_source(data)
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
    value = re.sub(r"[\\/:*?"<>|]+", "-", str(value))
    value = re.sub(r"[^A-Za-z0-9._ -]+", "-", value)
    value = re.sub(r"\\s+", " ", value).strip(" .-")
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
    info = inspect_episode_stream(episode_id)
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
                    "stream": inspect_episode_stream(eid),
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
