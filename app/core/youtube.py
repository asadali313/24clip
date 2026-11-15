"""Official YouTube Data API v3 publishing: multi-channel OAuth2 + uploads.

Setup the user must do once (free):
  1. Create a Google Cloud project and enable "YouTube Data API v3".
  2. Create an OAuth 2.0 Client ID of type "Desktop app".
  3. Download it as client_secret.json into
     D:\\AI_Clip_Generator\\credentials\\client_secret.json

This app NEVER asks for a Google password; auth happens on Google's own
consent page. Access+refresh tokens are stored per channel on D: only and are
never written to logs. Uploads use the resumable protocol. Each upload costs
~1600 quota units of the default 10,000/day - about 6 uploads per day.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .. import config

CREDENTIALS_DIR = config.ROOT / "credentials"
CLIENT_SECRET = CREDENTIALS_DIR / "client_secret.json"   # preferred name
TOKENS_DIR = CREDENTIALS_DIR / "tokens"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


class YouTubeError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Client-secret discovery + validation
# ---------------------------------------------------------------------------
def _validate_secret_json(path: Path) -> str | None:
    """Return None when the file is a usable Desktop/Web OAuth client,
    otherwise a specific human-readable reason why it is not."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"not valid JSON ({e.msg} at line {e.lineno})"
    except OSError as e:
        return f"cannot be read ({e.strerror})"
    if not isinstance(data, dict):
        return "unexpected JSON structure (expected an object)"
    for kind in ("installed", "web"):
        block = data.get(kind)
        if isinstance(block, dict):
            missing = [k for k in ("client_id", "client_secret",
                                   "auth_uri", "token_uri")
                       if not block.get(k)]
            if missing:
                return (f"{kind} client is missing fields: "
                        f"{', '.join(missing)}")
            if kind == "installed" and not block.get("redirect_uris"):
                # run_local_server overrides the redirect; a missing list is
                # tolerated but unusual
                pass
            return None                      # valid
    if "web" in data or "installed" in data:
        return "client block present but empty/invalid"
    return ("no 'installed' (Desktop app) or 'web' client found - is this "
            "the downloaded OAuth client JSON?")


def find_client_secret() -> Path | None:
    """Locate the user's OAuth client file in the credentials folder.

    Accepts the preferred name (client_secret.json) plus any Google download
    name (client_secret_XXXX.apps.googleusercontent.com.json, or a double
    extension like client_secret.json.json) - the first VALID file wins.
    """
    if not CREDENTIALS_DIR.exists():
        return None
    candidates: list[Path] = []
    if CLIENT_SECRET.exists():
        candidates.append(CLIENT_SECRET)
    for p in sorted(CREDENTIALS_DIR.glob("client_secret*.json")):
        if p not in candidates:
            candidates.append(p)
    for p in sorted(CREDENTIALS_DIR.glob("*.json")):
        if p not in candidates and p.parent.name == "credentials":
            candidates.append(p)             # last resort: any json there
    for p in candidates:
        if p.name == "client_secret.json" or "client_secret" in p.name:
            if _validate_secret_json(p) is None:
                return p
    # any other json in the folder that happens to validate
    for p in candidates:
        if _validate_secret_json(p) is None:
            return p
    return None


def client_secret_error() -> str:
    """Why no usable OAuth client was found ('' = one was found)."""
    if not CREDENTIALS_DIR.exists():
        return f"The credentials folder does not exist: {CREDENTIALS_DIR}"
    candidates = [CLIENT_SECRET] + sorted(
        CREDENTIALS_DIR.glob("client_secret*.json")) + sorted(
        CREDENTIALS_DIR.glob("*.json"))
    seen: list[Path] = []
    for p in candidates:
        if p not in seen:
            seen.append(p)
    if not seen:
        return (f"No client_secret JSON file found in {CREDENTIALS_DIR}. "
                "Save the downloaded Google OAuth client there.")
    reasons = []
    for p in seen[:4]:
        r = _validate_secret_json(p)
        if r is None:
            return ""                         # valid file exists
        reasons.append(f"• {p.name}: {r}")
    return ("The OAuth client file(s) in the credentials folder are not "
            "usable:\n" + "\n".join(reasons))


def has_client_secret() -> bool:
    return find_client_secret() is not None


def setup_instructions() -> str:
    return (
        "To connect a YouTube channel (one-time, free):\n"
        "1. Go to console.cloud.google.com and create a project.\n"
        "2. Enable 'YouTube Data API v3' (APIs & Services -> Library).\n"
        "3. Create OAuth consent screen (External, add yourself as tester)\n"
        "   and an OAuth Client ID of type 'Desktop app'.\n"
        "4. Download the client secret JSON and save it as:\n"
        f"   {CLIENT_SECRET}\n"
        "5. Click 'Connect channel' - Google's own consent page opens in "
        "your browser.\n\n"
        "Note: uploads consume YouTube API quota (~1,600 units each of the "
        "default 10,000/day, i.e. about 6 uploads per day). Newly created "
        "API projects must add the channel as a test user before upload is "
        "unblocked by YouTube.")


def _ensure_dirs() -> None:
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def connect_channel_local(timeout_s: int = 600) -> dict:
    """OAuth via a localhost redirect server (opens the system browser).

    Blocks until the user completes consent. Returns channel info dict and
    stores the tokens on D:. Raises YouTubeError on cancel/timeout.
    """
    _ensure_dirs()
    secret = find_client_secret()
    if secret is None:
        raise YouTubeError(client_secret_error())
    from google_auth_oauthlib.flow import InstalledAppFlow
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secret), scopes=SCOPES)
    except ValueError as e:
        # google-auth raises ValueError for malformed/unsupported files
        raise YouTubeError(
            f"The OAuth client file {secret.name} could not be used: {e}")
    creds = flow.run_local_server(
        host="localhost", port=0,
        authorization_prompt_message="Waiting for Google consent...",
        success_message="Authorization complete. You can close this tab.",
        open_browser=True, timeout_seconds=timeout_s)

    service = _service_with_creds(creds)
    resp = service.channels().list(part="snippet,contentDetails,statistics",
                                   mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise YouTubeError(
            "This Google account has no YouTube channel. Sign in with the "
            "account that owns the channel you want to publish to.")
    ch = items[0]
    channel_id = ch["id"]
    info = {
        "channel_id": channel_id,
        "title": ch["snippet"]["title"],
        "description": ch["snippet"].get("description", "")[:200],
        "thumbnail": ch["snippet"].get("thumbnails", {})
        .get("default", {}).get("url", ""),
        "subscribers": ch.get("statistics", {}).get("subscriberCount", "?"),
        "connected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    token_file = _token_path(channel_id)
    token_file.write_text(json.dumps({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
        "channel": info,
    }, indent=2), encoding="utf-8")
    return info


def _token_path(channel_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in channel_id)
    return TOKENS_DIR / f"{safe}.json"


def _load_creds(channel_id: str):
    from google.oauth2.credentials import Credentials
    tp = _token_path(channel_id)
    if not tp.exists():
        raise YouTubeError("Channel is not connected.")
    data = json.loads(tp.read_text(encoding="utf-8"))
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes", SCOPES))


def _save_creds(channel_id: str, creds) -> None:
    tp = _token_path(channel_id)
    data = json.loads(tp.read_text(encoding="utf-8"))
    data.update({
        "token": creds.token,
        "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None)
        else None,
    })
    tp.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _service_with_creds(creds):
    from googleapiclient.discovery import build
    return build(API_SERVICE_NAME, API_VERSION, credentials=creds,
                 cache_discovery=False)


def get_service(channel_id: str):
    """Authorized service; refreshes expired tokens automatically."""
    creds = _load_creds(channel_id)
    from google.auth.transport.requests import Request
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_creds(channel_id, creds)
        else:
            raise YouTubeError(
                "Authorization expired or revoked - reconnect this channel.")
    return _service_with_creds(creds)


def list_channels() -> list[dict]:
    """Connected channels from stored tokens (no network needed)."""
    if not TOKENS_DIR.exists():
        return []
    out = []
    for tp in TOKENS_DIR.glob("*.json"):
        try:
            data = json.loads(tp.read_text(encoding="utf-8"))
            ch = data.get("channel", {})
            if ch.get("channel_id"):
                out.append(ch)
        except (json.JSONDecodeError, OSError):
            continue
    return out


def disconnect_channel(channel_id: str) -> None:
    """Revoke the token server-side (best effort) and delete it locally."""
    try:
        creds = _load_creds(channel_id)
        import google.auth.transport.requests as gtr
        try:
            import requests as _requests
        except ImportError:
            _requests = None
        import urllib.request
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/revoke",
            data=f"token={creds.token}".encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass                                   # revoke is best effort
    _token_path(channel_id).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Upload (resumable)
# ---------------------------------------------------------------------------
CATEGORIES = {
    "gaming": "20",          # Gaming
    "entertainment": "24",   # Entertainment
    "education": "27",       # Education
    "people": "22",          # People & Blogs
}


def map_api_error(e: Exception) -> str:
    """Translate google-api errors into actionable messages (no secrets)."""
    s = str(e)
    if "quotaExceeded" in s or "quota" in s.lower() and "exceeded" in s.lower():
        return ("YouTube API quota exceeded for today (uploads cost ~1,600 "
                "units of the default 10,000/day). Try again after the daily "
                "reset, or raise the quota in your Google Cloud project.")
    if "invalid_grant" in s or "expired or revoked" in s:
        return ("Authorization expired or revoked - reconnect the channel "
                "on the Publishing screen.")
    if "forbidden" in s.lower() or "403" in s:
        if "test users" in s or "not been used" in s or "unverified" in s:
            return ("YouTube blocks uploads for apps in testing until the "
                    "channel is added as a Test User in your Google Cloud "
                    "OAuth consent screen (and the upload may need review).")
        return f"YouTube rejected the request (403): {s[:200]}"
    if "uploadLimitExceeded" in s:
        return "YouTube: the account has reached its upload limit for today."
    if "duplicateVideo" in s:
        return "YouTube detected this video as a duplicate upload."
    if "timeout" in s.lower() or "connection" in s.lower():
        return f"Network interruption: {s[:180]}"
    return s[:300]


def upload_video(channel_id: str, video_file: Path, metadata: dict,
                 progress_cb=None) -> dict:
    """Resumable upload. metadata keys: title, description, tags, category_id,
    privacy_status ('public'|'unlisted'|'private'), made_for_kids,
    publish_at (ISO UTC, optional).

    Returns {'video_id', 'url', 'privacy'} once YouTube confirms the upload.
    """
    if not video_file.exists() or video_file.stat().st_size < 1024:
        raise YouTubeError(f"Video file missing or invalid: {video_file}")
    title = str(metadata.get("title", "")).strip()[:100]
    if not title:
        raise YouTubeError("A title is required before publishing.")
    privacy = metadata.get("privacy_status", "private")
    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": bool(metadata.get("made_for_kids", False)),
    }
    publish_at = metadata.get("publish_at")
    if publish_at:
        # scheduled: must be private until YouTube publishes it
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    body = {
        "snippet": {
            "title": title,
            "description": str(metadata.get("description", ""))[:4900],
            "tags": [str(t)[:50] for t in metadata.get("tags", [])][:30],
            "categoryId": str(metadata.get("category_id", "22")),
        },
        "status": status,
    }
    service = get_service(channel_id)
    from googleapiclient.http import MediaFileUpload
    size = video_file.stat().st_size
    media = MediaFileUpload(str(video_file), chunksize=8 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body,
                                      media_body=media)
    response = None
    retries = 0
    while response is None:
        try:
            status_r, response = request.next_chunk()
            if status_r and progress_cb:
                progress_cb(status_r.progress(), size)
        except Exception as e:                 # transient network/API errors
            retries += 1
            if retries > 5:
                raise YouTubeError(map_api_error(e))
            time.sleep(min(60, 2 ** retries))
    vid = response.get("id")
    if not vid:
        raise YouTubeError(f"Upload did not return a video id: {response}")
    return {"video_id": vid, "url": f"https://youtu.be/{vid}",
            "privacy": response.get("status", {}).get("privacyStatus",
                                                      privacy)}


def get_video_status(channel_id: str, video_id: str) -> dict:
    """Real processing/publication status straight from the YouTube API."""
    service = get_service(channel_id)
    resp = service.videos().list(
        part="status,processingDetails,snippet",
        id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        return {"exists": False}
    v = items[0]
    st = v.get("status", {})
    pd = v.get("processingDetails", {})
    sn = v.get("snippet", {})
    return {
        "exists": True,
        "upload_status": st.get("uploadStatus", ""),
        "processing_status": pd.get("processingStatus", ""),
        "privacy_status": st.get("privacyStatus", ""),
        "publish_at": st.get("publishAt", ""),
        "failure_reason": st.get("failureReason", ""),
        "rejection_reason": st.get("rejectionReason", ""),
        "title": sn.get("title", ""),
        "thumbnail": (sn.get("thumbnails", {}).get("medium", {})
                      .get("url") or sn.get("thumbnails", {})
                      .get("default", {}).get("url", "")),
    }


def wait_for_processing(channel_id: str, video_id: str,
                        progress_cb=None, timeout_s: int = 900,
                        poll_s: int = 15) -> dict:
    """Poll the API until YouTube finishes processing (or it fails)."""
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        last = get_video_status(channel_id, video_id)
        if progress_cb:
            progress_cb(last)
        us = last.get("upload_status", "")
        ps = last.get("processing_status", "")
        if us in ("rejected", "failed", "deleted"):
            return last                       # failure details included
        if us == "processed" and ps in ("succeeded", "terminated", ""):
            return last
        time.sleep(poll_s)
    return last                              # timeout: caller decides


def upload_captions(channel_id: str, video_id: str, srt_file: Path,
                    language: str = "en") -> bool:
    """Attach a local .srt as the video's caption track (real API call)."""
    if not srt_file.exists():
        raise YouTubeError(f"Captions file missing: {srt_file}")
    service = get_service(channel_id)
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(srt_file), mimetype="application/octet-stream")
    try:
        service.captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": language,
                              "name": "English (auto-generated by AI Clip "
                                      "Generator)", "isDraft": False}},
            media_body=media).execute()
        return True
    except Exception as e:
        raise YouTubeError(map_api_error(e))
