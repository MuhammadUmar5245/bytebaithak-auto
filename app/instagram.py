"""Official Instagram API. Only publishing and insights; nothing else is automated.

Two token types work, picked automatically from the token itself:
  IGAA... (Instagram Login)                 -> graph.instagram.com
  EAA...  (Facebook Login / business system user with instagram_content_publish) -> graph.facebook.com
The publishing endpoints (/{ig-user-id}/media, /media_publish, ...) are the same on both hosts.
"""
import hashlib
import json
import logging
import time

import requests

from .config import S

log = logging.getLogger("instagram")
TOKEN_FILE = S.data_dir / "ig_token.json"


class IGError(RuntimeError):
    pass


def _facebook_token():
    return token().startswith("EAA")


def _base():
    host = "https://graph.facebook.com" if _facebook_token() else "https://graph.instagram.com"
    return host + (f"/{S.ig_version}" if S.ig_version else "")


def _env_fingerprint():
    return hashlib.sha256(S.ig_token.encode()).hexdigest()[:16] if S.ig_token else ""


def token():
    # Use the refreshed token, unless the owner has since put a different token in .env.
    if TOKEN_FILE.exists():
        try:
            saved = json.loads(TOKEN_FILE.read_text())
            if saved.get("env_token") in (None, _env_fingerprint()) or not S.ig_token:
                return saved["access_token"]
        except (ValueError, KeyError):
            pass
    if not S.ig_token:
        raise IGError("IG_ACCESS_TOKEN missing in .env")
    return S.ig_token


def _call(method, path, **params):
    params["access_token"] = token()
    url = path if path.startswith("http") else f"{_base()}/{path.lstrip('/')}"
    r = requests.request(method, url, params=params if method == "GET" else None,
                         data=params if method != "GET" else None, timeout=60)
    try:
        data = r.json()
    except ValueError:
        raise IGError(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise IGError(f"{err.get('type', 'error')} {err.get('code', r.status_code)}: {err.get('message', r.text[:300])}")
    return data


def me():
    if _facebook_token():
        # on graph.facebook.com /me is the Facebook user, so ask for the Instagram account directly
        if not S.ig_user:
            raise IGError("IG_USER_ID missing in .env")
        d = _call("GET", S.ig_user, fields="id,username,media_count")
        return dict(d, token="facebook (graph.facebook.com)")
    return _call("GET", "me", fields="user_id,username,account_type,media_count")


def quota():
    d = _call("GET", f"{S.ig_user}/content_publishing_limit", fields="quota_usage,config")
    row = (d.get("data") or [{}])[0]
    return row.get("quota_usage", 0), (row.get("config") or {}).get("quota_total", 100)


def _wait(container_id, timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        st = _call("GET", container_id, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise IGError(f"container {container_id} status {st}")
        time.sleep(5)
    raise IGError(f"container {container_id} not ready after {timeout}s")


def _publish(container_id):
    _wait(container_id)
    media_id = _call("POST", f"{S.ig_user}/media_publish", creation_id=container_id)["id"]
    # The post is live now. A failed permalink lookup must not look like a failed publish,
    # or a /retry would post it twice.
    try:
        link = _call("GET", media_id, fields="permalink").get("permalink", "")
    except (IGError, requests.RequestException) as e:
        log.warning("published %s but permalink lookup failed: %s", media_id, e)
        link = ""
    return media_id, link


def publish_carousel(image_urls, caption, alt_text=None):
    if not 2 <= len(image_urls) <= 10:
        raise IGError("a carousel needs 2-10 images")
    children = []
    for url in image_urls:
        params = {"image_url": url, "is_carousel_item": "true"}
        if alt_text:
            params["alt_text"] = alt_text[:1000]
        try:
            cid = _call("POST", f"{S.ig_user}/media", **params)["id"]
        except IGError:
            if not alt_text:
                raise
            params.pop("alt_text")  # older API versions reject alt_text on carousel items
            cid = _call("POST", f"{S.ig_user}/media", **params)["id"]
        children.append(cid)
    for cid in children:
        _wait(cid)
    parent = _call("POST", f"{S.ig_user}/media", media_type="CAROUSEL",
                   children=",".join(children), caption=caption)["id"]
    return _publish(parent)


def publish_reel(video_url, caption, cover_url=None):
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"}
    if cover_url:
        params["cover_url"] = cover_url
    cid = _call("POST", f"{S.ig_user}/media", **params)["id"]
    return _publish(cid)


def insights(media_id):
    """Metric names differ by media type and API version, so ask one by one and keep what works."""
    out = {}
    for m in ("reach", "views", "saved", "shares", "likes", "comments", "total_interactions"):
        try:
            d = _call("GET", f"{media_id}/insights", metric=m)
            out[m] = d["data"][0]["values"][0]["value"]
        except (IGError, KeyError, IndexError):
            continue
    return out


def refresh_token():
    if _facebook_token():
        log.info("facebook/system-user token: no ig_refresh_token needed, skipping refresh")
        return None
    d = _call("GET", "https://graph.instagram.com/refresh_access_token", grant_type="ig_refresh_token")
    TOKEN_FILE.write_text(json.dumps({"access_token": d["access_token"], "expires_in": d.get("expires_in"),
                                      "env_token": _env_fingerprint(),
                                      "refreshed_at": time.strftime("%Y-%m-%d %H:%M")}))
    return d.get("expires_in")
