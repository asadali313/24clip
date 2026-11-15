"""Video import from URLs.

- YouTube and other supported platforms via yt-dlp (open source, technically
  supported method; platform terms and copyright still apply).
- Direct video file URLs (mp4/webm/mkv) via streaming download.

Nothing is faked: errors from private/login-required/unsupported sources are
surfaced with clear messages. All downloads land on D:.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .. import config

DOWNLOADS_DIR = config.ROOT / "downloads"
SUPPORTED_DIRECT = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts")


class ImportURLError(RuntimeError):
    pass


def is_direct_url(url: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return any(path.endswith(ext) for ext in SUPPORTED_DIRECT)


def probe_info(url: str) -> dict:
    """Metadata for a URL without downloading. Raises ImportURLError with a
    clear message when the source is unavailable/unsupported."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ImportURLError("Not a valid http(s) URL.")
    if is_direct_url(url):
        # cheap HEAD probe for direct files
        proc = subprocess.run(
            [config.ffprobe(), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", url],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if proc.returncode != 0:
            raise ImportURLError(
                "The direct video URL could not be opened (server refused, "
                "moved, or is not a video stream).")
        data = json.loads(proc.stdout)
        fmt = data.get("format", {})
        if not any(s.get("codec_type") == "video"
                   for s in data.get("streams", [])):
            raise ImportURLError("That URL does not point to a video file.")
        return {
            "kind": "direct",
            "url": url,
            "title": Path(url).name or "direct video",
            "duration": float(fmt.get("duration", 0) or 0),
            "size_bytes": int(fmt.get("size", 0) or 0),
        }
    # platform URLs via yt-dlp (info only)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys_exec(), "-m", "yt_dlp", "--no-playlist", "--no-warnings",
         "--dump-single-json", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=str(DOWNLOADS_DIR))
    if proc.returncode != 0:
        raise ImportURLError(_map_ytdlp_error(proc.stderr or ""))
    data = json.loads(proc.stdout)
    if data.get("_type") == "playlist":
        entries = data.get("entries") or []
        if not entries:
            raise ImportURLError("That playlist is empty or private.")
        data = entries[0]
    return {
        "kind": "platform",
        "url": url,
        "title": data.get("title", "video"),
        "duration": float(data.get("duration", 0) or 0),
        "thumbnail": data.get("thumbnail", ""),
        "uploader": data.get("uploader", ""),
    }


def download(url: str, progress_cb=None) -> Path:
    """Download the video to D:\\AI_Clip_Generator\\downloads and return the
    local path."""
    info = probe_info(url)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if info["kind"] == "direct":
        dest = _safe_dest(info["title"])
        return _download_direct(url, dest, progress_cb)
    out_tmpl = str(DOWNLOADS_DIR / "%(title).80s.%(ext)s")
    cmd = [sys_exec(), "-m", "yt_dlp", "--no-playlist", "--no-warnings",
           "-f", "bv*[height<=1080]+ba/b[height<=1080]",
           "--merge-output-format", "mp4",
           "-o", out_tmpl, "--newline", url]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(DOWNLOADS_DIR))
    assert proc.stdout is not None
    last_file: Path | None = None
    last_line = ""
    for line in proc.stdout:
        line = line.strip()
        m = re.search(r"\[download\]\s+(\d{1,3}(?:\.\d)?)%", line)
        if m and progress_cb:
            progress_cb(float(m.group(1)) / 100.0)
        mf = re.match(r"\[Merger\] Destination: (.+)", line) or \
            re.match(r"\[download\] Destination: (.+)", line)
        if mf:
            last_file = Path(mf.group(1))
        if "ERROR" in line.upper() or line.startswith("["):
            last_line = line
    if proc.returncode != 0:
        raise ImportURLError("Download failed. " + (last_line or
                             "yt-dlp exited with an error."))
    # figure out what landed
    if last_file and last_file.exists():
        return last_file
    newest = max(DOWNLOADS_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime,
                 default=None)
    if newest:
        return newest
    raise ImportURLError("Download finished but no video file was found.")


def _map_ytdlp_error(stderr: str) -> str:
    s = stderr.lower()
    if "private video" in s:
        return "That video is private."
    if "sign in to confirm" in s or "cookies" in s:
        return ("The platform requires sign-in for this content - automated "
                "download is not available for it.")
    if "unavailable" in s or "removed" in s:
        return "That video is unavailable or was removed."
    if "unsupported url" in s:
        return "This source is not supported for direct import."
    if "copyright" in s:
        return "The platform blocked this download (copyright restriction)."
    if "not a valid url" in s or "unable to download" in s:
        return "The URL could not be processed."
    return (stderr.strip().splitlines() or ["yt-dlp reported an error"])[-1][:280]


def _safe_dest(title: str) -> Path:
    name = re.sub(r"[^a-zA-Z0-9 ._-]", "_", title)[:80] or "video"
    return DOWNLOADS_DIR / f"{name}.mp4"


def _download_direct(url: str, dest: Path, progress_cb=None) -> Path:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except Exception as e:  # noqa: BLE001
        raise ImportURLError(f"Could not open the URL: {e}")
    total = int(resp.headers.get("Content-Length", 0) or 0)
    tmp = dest.with_suffix(".part")
    done = 0
    with open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress_cb and total:
                progress_cb(min(1.0, done / total))
    resp.close()
    if dest.exists():
        dest.unlink()
    tmp.replace(dest)
    return dest


def sys_exec() -> str:
    import sys
    return sys.executable
