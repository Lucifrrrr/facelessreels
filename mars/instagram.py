"""Offizielle Instagram API (Instagram Login, graph.instagram.com).

Benötigte Berechtigungen: instagram_business_basic, instagram_business_content_publish,
instagram_business_manage_comments. Zugangsdaten nur aus Umgebungsvariablen:
IG_ACCESS_TOKEN (langlebig, 60 Tage) und IG_USER_ID.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .common import STATE_DIR, get_logger, read_json, secret, write_json

log = get_logger()
TOKEN_META = STATE_DIR / "ig_token_meta.json"


class InstagramError(RuntimeError):
    pass


def _base(cfg):
    return f"{cfg['instagram']['api_host']}/{cfg['instagram']['api_version']}"


def _call(cfg, method: str, path: str, params: dict | None = None, timeout: int = 60, retries: int = 3) -> dict:
    params = dict(params or {})
    params["access_token"] = secret("IG_ACCESS_TOKEN")
    url = path if path.startswith("http") else f"{_base(cfg)}/{path.lstrip('/')}"
    data = None
    if method == "GET":
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method), timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(body).get("error", {})
            except ValueError:
                err = {"message": body[:300]}
            transient = e.code >= 500 or err.get("is_transient") or err.get("code") in (1, 2, 4, 17, 341)
            if transient and attempt < retries:
                time.sleep(10 * (attempt + 1))
                continue
            # Token nie protokollieren
            raise InstagramError(f"HTTP {e.code} {method} {path}: {err.get('message')} (code {err.get('code')}, "
                                 f"subcode {err.get('error_subcode')})") from e
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(10 * (attempt + 1))
                continue
            raise InstagramError(f"Netzwerkfehler {path}: {e.reason}") from e
    raise InstagramError("unerreichbar")


def user_id() -> str:
    return secret("IG_USER_ID")


def account(cfg) -> dict:
    return _call(cfg, "GET", "me", {"fields": "user_id,username,account_type,followers_count,media_count"})


def followers(cfg) -> int:
    return int(_call(cfg, "GET", user_id(), {"fields": "followers_count"})["followers_count"])


def comments(cfg, media_id: str) -> list[dict]:
    out = []
    res = _call(cfg, "GET", f"{media_id}/comments", {"fields": "id,text,timestamp,username,from", "limit": 50})
    while True:
        out.extend(res.get("data", []))
        nxt = res.get("paging", {}).get("next")
        if not nxt or len(out) > 20000:
            break
        with urllib.request.urlopen(nxt, timeout=60) as r:
            res = json.loads(r.read())
    return out


def recent_media(cfg, limit: int = 10) -> list[dict]:
    return _call(cfg, "GET", f"{user_id()}/media",
                 {"fields": "id,caption,timestamp,media_product_type,permalink", "limit": limit}).get("data", [])


def publishing_quota(cfg) -> dict:
    return _call(cfg, "GET", f"{user_id()}/content_publishing_limit", {"fields": "quota_usage,config"})


# --------------------------------------------------------------------------- Veröffentlichung

def create_reel_container(cfg, video: Path, caption: str) -> str:
    """Resumable Upload: keine öffentliche Video-URL / kein Hosting nötig."""
    res = _call(cfg, "POST", f"{user_id()}/media", {
        "media_type": "REELS", "upload_type": "resumable", "caption": caption, "share_to_feed": "true",
    })
    container, uri = res["id"], res.get("uri")
    if not uri:
        raise InstagramError(f"Keine Upload-URI erhalten: {res}")
    size = video.stat().st_size
    data = video.read_bytes()
    for attempt in range(4):
        req = urllib.request.Request(uri, data=data, method="POST", headers={
            "Authorization": f"OAuth {secret('IG_ACCESS_TOKEN')}", "offset": "0", "file_size": str(size),
            "Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                body = json.loads(r.read() or b"{}")
            if body.get("success") is False:
                raise InstagramError(f"Upload abgelehnt: {body}")
            break
        except (urllib.error.URLError, InstagramError) as e:
            if attempt == 3:
                raise InstagramError(f"Video-Upload fehlgeschlagen: {e}") from e
            time.sleep(15 * (attempt + 1))
    return container


def wait_container(cfg, container: str, minutes: int = 15) -> str:
    deadline = time.time() + minutes * 60
    status = None
    while time.time() < deadline:
        r = _call(cfg, "GET", container, {"fields": "status_code,status"})
        status = r.get("status_code")
        if status in ("FINISHED", "PUBLISHED"):
            return status
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Container {container}: {status} {r.get('status')}")
        time.sleep(30)
    raise InstagramError(f"Container {container} nach {minutes} min nicht fertig ({status})")


def publish(cfg, container: str) -> dict:
    r = _call(cfg, "POST", f"{user_id()}/media_publish", {"creation_id": container}, retries=1)
    media_id = r["id"]
    info = _call(cfg, "GET", media_id, {"fields": "id,permalink,media_product_type,timestamp"})
    return info


def already_published(cfg, tag: str) -> dict | None:
    """Doppelte Veröffentlichung verhindern: Episoden-Hashtag in den letzten Beiträgen suchen."""
    for m in recent_media(cfg, 12):
        if tag in (m.get("caption") or ""):
            return m
    return None


# --------------------------------------------------------------------------- Token

def refresh_token_if_needed(cfg, max_age_days: int = 20) -> bool:
    meta = read_json(TOKEN_META, {})
    last = meta.get("refreshed_at")
    if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days < max_age_days:
        return False
    url = (f"{cfg['instagram']['api_host']}/refresh_access_token?grant_type=ig_refresh_token&"
           f"access_token={urllib.parse.quote(secret('IG_ACCESS_TOKEN'))}")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise InstagramError(f"Token-Erneuerung fehlgeschlagen: HTTP {e.code}") from e
    new = res["access_token"]
    _persist_token(new)
    os.environ["IG_ACCESS_TOKEN"] = new
    write_json(TOKEN_META, {"refreshed_at": datetime.now(timezone.utc).isoformat(), "expires_in": res.get("expires_in")})
    log.info("Instagram-Token erneuert (gültig %s Tage)", int(res.get("expires_in", 0)) // 86400)
    return True


def _persist_token(token: str) -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        # Benötigt Secret GH_SECRETS_TOKEN (fein granular, nur "Secrets: write" für dieses Repo).
        env = dict(os.environ, GH_TOKEN=os.environ.get("GH_SECRETS_TOKEN", ""))
        subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN", "--body", token], check=True, env=env,
                       capture_output=True)
    elif os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "IG_ACCESS_TOKEN", 0, winreg.REG_SZ, token)
