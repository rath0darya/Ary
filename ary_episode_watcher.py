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
ARY_WEB = os.environ.get("ARY_WEB_URL", "https://arydigital.tv").rstrip("/")
SERIES_ID = os.environ.get(
    "ARY_SERIES_ID",
    "6a57868b5bf57c474cc00a50",
)
SERIES_NAME = os.environ.get("ARY_SERIES_NAME", "Dar-E-Nijaat")
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


def catalogue() -> list[dict[str, Any]]:
    with CACHE_LOCK:
        if CACHE.get("episodes"):
            return CACHE["episodes"]

    episodes: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page in range(1, 101):
        data = api_json(
            f"/api/v2/cdn/pg/{quote(SERIES_ID, safe='')}?page={page}&limit=50"
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

    if not episodes:
        raise RuntimeError("ARY returned no episodes for the configured series.")

    episodes.sort(
        key=lambda x: (
            int(str(x["number"]).strip()) if str(x["number"]).strip().isdigit() else -1
        ),
        reverse=True,
    )

    with CACHE_LOCK:
        CACHE["episodes"] = episodes
        CACHE["episodes_at"] = time.time()
    return episodes


def find_episode(episode_id: str) -> dict[str, Any] | None:
    for item in catalogue():
        if item["id"] == episode_id:
            return item
    return None


def parse_attributes(line: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line):
        value = match.group(2)
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        attrs[match.group(1)] = value
    return attrs


def parse_hls_master(text: str, base_url: str) -> dict[str, Any]:
    variants: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    pending_variant: dict[str, str] | None = None
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = parse_attributes(line.split(":", 1)[1])
            if attrs.get("TYPE") == "SUBTITLES":
                subtitles.append({
                    "group_id": attrs.get("GROUP-ID"),
                    "name": attrs.get("NAME"),
                    "language": attrs.get("LANGUAGE"),
                    "default": attrs.get("DEFAULT") == "YES",
                    "forced": attrs.get("FORCED") == "YES",
                    "uri": urljoin(base_url, attrs["URI"]) if attrs.get("URI") else None,
                })
        elif line.startswith("#EXT-X-STREAM-INF:"):
            pending_variant = parse_attributes(line.split(":", 1)[1])
        elif pending_variant is not None and not line.startswith("#"):
            attrs = pending_variant
            width = height = None
            resolution = attrs.get("RESOLUTION", "")
            match = re.match(r"(\d+)x(\d+)$", resolution)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
            variants.append({
                "width": width,
                "height": height,
                "bandwidth": int(attrs["BANDWIDTH"]) if attrs.get("BANDWIDTH", "").isdigit() else None,
                "average_bandwidth": (
                    int(attrs["AVERAGE-BANDWIDTH"])
                    if attrs.get("AVERAGE-BANDWIDTH", "").isdigit()
                    else None
                ),
                "codecs": attrs.get("CODECS"),
                "frame_rate": attrs.get("FRAME-RATE"),
                "audio": attrs.get("AUDIO"),
                "subtitles_group": attrs.get("SUBTITLES"),
                "uri": urljoin(base_url, line),
            })
            pending_variant = None

    return {
        "variants": variants,
        "subtitles": subtitles,
        "is_master": bool(variants),
    }


def choose_best_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [
        item for item in variants
        if item.get("uri") and item.get("height")
    ]
    if not usable:
        return None
    usable.sort(
        key=lambda item: (
            item.get("height") or 0,
            item.get("width") or 0,
            item.get("bandwidth") or 0,
            item.get("average_bandwidth") or 0,
        ),
        reverse=True,
    )
    return usable[0]


def stream_source(episode_id: str) -> str:
    data = api_json(f"/api/cdn/ep/{quote(episode_id, safe='')}")
    episode = data.get("episode") if isinstance(data, dict) else None
    if isinstance(episode, list):
        episode = episode[0] if episode else {}
    if not isinstance(episode, dict):
        episode = data if isinstance(data, dict) else {}

    source = (
        episode.get("videoSource")
        or episode.get("video_source")
        or episode.get("source")
        or episode.get("url")
    )
    if isinstance(source, dict):
        source = (
            source.get("url")
            or source.get("src")
            or source.get("hls")
            or source.get("videoSource")
        )
    if not isinstance(source, str) or not source.strip():
        raise RuntimeError("ARY did not return a videoSource for this episode.")
    return source.strip()


def inspect_episode_stream(episode_id: str) -> dict[str, Any]:
    source = stream_source(episode_id)
    if not re.match(r"^https?://", source):
        source = urljoin(ARY_BASE + "/", source)

    text = http_text(
        source,
        {
            **api_headers(),
            "Referer": ARY_WEB + "/",
        },
        timeout=30,
    )
    parsed = parse_hls_master(text, source)
    parsed["master_url"] = source
    parsed["source"] = source
    parsed["best"] = choose_best_variant(parsed["variants"])

    if not parsed["variants"] and text.lstrip().startswith("#EXTM3U"):
        parsed["best"] = {
            "width": None,
            "height": None,
            "bandwidth": None,
            "average_bandwidth": None,
            "codecs": None,
            "uri": source,
        }
        parsed["variants"] = [parsed["best"]]

    return parsed


def safe_filename(value: str) -> str:
    value = unquote(str(value or "")).strip()
    value = re.sub(r"[\\/:*?"<>|]+", "-", value)
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip(".- ")
    return value or "episode"


def ensure_download_dir():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def unique_output(number: Any, quality: str) -> Path:
    number_text = safe_filename(str(number)) if number not in (None, "") else "Episode"
    stem = f"ARY_{safe_filename(SERIES_NAME)}_Episode-{number_text}_{quality}"
    candidate = DOWNLOAD_DIR / f"{stem}.mp4"
    counter = 2
    while candidate.exists():
        candidate = DOWNLOAD_DIR / f"{stem}_{counter}.mp4"
        counter += 1
    return candidate


def ffprobe_resolution(path: Path) -> str | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        value = result.stdout.strip()
        return value or None
    except (OSError, subprocess.SubprocessError):
        return None


def ffprobe_duration(url: str) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
        value = result.stdout.strip()
        return float(value) if value else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def update_job(job_id: str, **changes: Any):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(changes)


def run_download_job(job_id: str):
    with JOBS_LOCK:
        job = dict(JOBS[job_id])

    output = Path(job["output"])
    variant_url = job["variant_url"]
    duration = ffprobe_duration(variant_url)
    update_job(job_id, state="downloading", started=time.time(), duration=duration)

    if not shutil.which("ffmpeg"):
        update_job(job_id, state="error", error="ffmpeg is not installed.")
        return

    headers = (
        "Referer: " + ARY_WEB + "/\r\n"
        "Origin: " + ARY_WEB + "\r\n"
        "User-Agent: " + USER_AGENT + "\r\n"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-headers", headers,
        "-i", variant_url,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c", "copy",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(output),
    ]

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        with JOBS_LOCK:
            JOBS[job_id]["pid"] = process.pid

        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                    percent = (
                        min(99.0, max(0.0, seconds / duration * 100))
                        if duration
                        else None
                    )
                    update_job(
                        job_id,
                        out_time=seconds,
                        percent=percent,
                    )
                except ValueError:
                    pass
            elif line.startswith("speed="):
                update_job(job_id, speed=line.split("=", 1)[1])

        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait()

        if return_code != 0:
            if output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            update_job(
                job_id,
                state="error",
                error=stderr.strip()[-3000:] or f"ffmpeg exited with {return_code}",
                finished=time.time(),
            )
            return

        if not output.exists() or output.stat().st_size <= 0:
            update_job(
                job_id,
                state="error",
                error="FFmpeg completed without producing a usable file.",
                finished=time.time(),
            )
            return

        actual_resolution = ffprobe_resolution(output)
        update_job(
            job_id,
            state="completed",
            percent=100.0,
            size=output.stat().st_size,
            resolution=actual_resolution or job.get("resolution"),
            finished=time.time(),
            pid=None,
        )
    except Exception as exc:
        update_job(
            job_id,
            state="error",
            error=str(exc),
            finished=time.time(),
            pid=None,
        )


def start_download(episode_id: str, episode_number: Any = None, episode_title: str = ""):
    stream = inspect_episode_stream(episode_id)
    variants = stream.get("variants") or []
    variant = choose_best_variant(variants)
    if not variant:
        raise RuntimeError("No downloadable HLS quality is available.")

    height = int(variant["height"]) if variant.get("height") else 0
    width = int(variant["width"]) if variant.get("width") else 0
    quality = f"{height}p" if height else "best"

    ensure_download_dir()
    output = unique_output(episode_number, quality)

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "state": "queued",
        "episode_id": episode_id,
        "episode_number": episode_number,
        "title": episode_title or f"Episode {episode_number}",
        "quality": quality,
        "resolution": f"{width}x{height}" if width and height else None,
        "variant_url": variant["uri"],
        "output": str(output),
        "filename": output.name,
        "size": 0,
        "percent": 0,
        "speed": None,
        "duration": None,
        "out_time": 0,
        "error": None,
        "started": None,
        "finished": None,
        "pid": None,
        "source_bandwidth": variant.get("bandwidth"),
        "source_average_bandwidth": variant.get("average_bandwidth"),
        "source_codecs": variant.get("codecs"),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=run_download_job, args=(job_id,), daemon=True).start()
    return job


class Handler(BaseHTTPRequestHandler):
    server_version = "ARYEpisodeWatcher/2.0"

    def log_message(self, fmt, *args):
        print("[HTTP]", fmt % args)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if path == "/api/health":
                return json_response(self, {
                    "ok": True,
                    "series": SERIES_NAME,
                    "download_dir": str(DOWNLOAD_DIR),
                    "ffmpeg": bool(shutil.which("ffmpeg")),
                    "ffprobe": bool(shutil.which("ffprobe")),
                })

            if path == "/api/episodes":
                refresh = query.get("refresh", ["0"])[0] == "1"
                if refresh:
                    with CACHE_LOCK:
                        CACHE.clear()
                return json_response(self, {
                    "ok": True,
                    "series": SERIES_NAME,
                    "episodes": catalogue(),
                })

            if path == "/api/stream-info":
                episode_id = query.get("episode", [""])[0].strip()
                if not episode_id:
                    return json_response(self, {"ok": False, "error": "episode is required"}, 400)
                return json_response(self, {
                    "ok": True,
                    "episode": find_episode(episode_id),
                    "stream": inspect_episode_stream(episode_id),
                })

            if path == "/api/download":
                episode_id = query.get("episode", [""])[0].strip()
                if not episode_id:
                    return json_response(self, {"ok": False, "error": "episode is required"}, 400)
                episode = find_episode(episode_id)
                if not episode:
                    return json_response(self, {"ok": False, "error": "Unknown episode"}, 404)
                number = query.get("number", [episode.get("number")])[0]
                title = query.get("title", [episode.get("title", "")])[0]
                job = start_download(episode_id, number, title)
                safe_job = dict(job)
                safe_job.pop("variant_url", None)
                return json_response(self, {"ok": True, "job": safe_job})

            if path.startswith("/api/download/"):
                job_id = path.rsplit("/", 1)[-1]
                with JOBS_LOCK:
                    job = dict(JOBS.get(job_id) or {})
                if not job:
                    return json_response(self, {"ok": False, "error": "Job not found"}, 404)
                job.pop("variant_url", None)
                return json_response(self, {"ok": True, "job": job})

            if path == "/" or path == "/index.html":
                return self.serve_static("index.html")

            return json_response(self, {"ok": False, "error": "Not found"}, 404)
        except Exception as exc:
            return json_response(self, {"ok": False, "error": str(exc)}, 500)

    def serve_static(self, filename: str):
        path = (WEB_DIR / filename).resolve()
        if WEB_DIR.resolve() not in path.parents:
            return json_response(self, {"ok": False, "error": "Invalid path"}, 400)
        if not path.exists():
            return json_response(self, {"ok": False, "error": "UI file missing"}, 500)
        data = path.read_bytes()
        content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    ensure_download_dir()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ARY Episode Watcher listening on http://{HOST}:{PORT}")
    print(f"Series: {SERIES_NAME} ({SERIES_ID})")
    print(f"Downloads: {DOWNLOAD_DIR}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
