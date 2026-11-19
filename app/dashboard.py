"""Web dashboard: posts, approvals, Instagram performance, course progress, calendar and bot health.

Runs inside the worker on its own thread (python stdlib HTTP server, no extra packages).
On this PC: http://localhost:8787. On the internet: the Cloudflare quick tunnel (docker-compose service
`tunnel`) gives an https://<random>.trycloudflare.com link, which the bot posts to Discord.
With DASHBOARD_PASSWORD (10+ chars) set, every visitor must log in (session cookie, 5 wrong tries ->
15 min lock per IP). Without it, only this PC gets in and the tunnel is refused.
Buttons go through the same approval.handle() as Discord and also need a per-start secret token.
"""
import json
import logging
import mimetypes
import secrets
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import approval, db
from .config import PILLARS, S, now, today

log = logging.getLogger("dashboard")
PORT = 8080
TOKEN = secrets.token_urlsafe(24)
REFRESH_MIN = 30          # how often Instagram numbers are fetched
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}   # any port: docker maps it
_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                   autoescape=select_autoescape(["html", "j2"]))
_env.filters["q"] = lambda s: quote(str(s or ""), safe="")


# ---------------------------------------------------------------- data
def profile_url():
    return f"https://www.instagram.com/{S.handle.lstrip('@')}/"


def live():
    """Instagram numbers cached by the refresher thread: account, quota, insights per post."""
    try:
        return json.loads(db.kv_get("dash_live", "{}"))
    except ValueError:
        return {}


def refresh_live():
    from . import instagram
    out = live()
    try:
        out["account"] = instagram._call("GET", S.ig_user, fields="username,followers_count,media_count")
    except Exception as e:  # never break the dashboard because Instagram is slow
        log.warning("account info: %s", e)
    try:
        out["quota"] = list(instagram.quota())
    except Exception as e:
        log.warning("quota: %s", e)
    ins = out.get("insights", {})
    for p in db.query("status='published' AND ig_media_id IS NOT NULL"):
        try:
            ins[p["id"]] = instagram.insights(p["ig_media_id"])
        except Exception as e:
            log.warning("insights %s: %s", p["id"], e)
    out["insights"] = ins
    out["at"] = now().strftime("%Y-%m-%d %H:%M")
    db.kv_set("dash_live", json.dumps(out))
    return out


def _refresher():
    while True:
        try:
            refresh_live()
        except Exception:
            log.exception("dashboard refresh failed")
        time.sleep(REFRESH_MIN * 60)


def _thumb(p):
    """URL of the first slide / reel cover, served from /media/."""
    d = p.get("media_dir")
    if not d:
        return None
    try:
        rel = Path(d).relative_to(S.output_dir)
    except ValueError:
        return None
    if (S.output_dir / rel / "slide-01.jpg").exists():
        return f"/media/{rel.as_posix()}/slide-01.jpg"
    return None


def _video(p):
    d = p.get("media_dir")
    if d and p.get("format") == "reel" and (Path(d) / "reel.mp4").exists():
        return f"/media/{Path(d).relative_to(S.output_dir).as_posix()}/reel.mp4"
    return None


def _calendar():
    import csv
    with open(S.data_dir / "calendar.csv", newline="", encoding="utf-8") as f:
        return sorted(csv.DictReader(f), key=lambda r: (r["date"], r["slot"]))


def _with_status(rows):
    """Calendar rows joined with their post in the database (or 'scheduled' if not made yet)."""
    from .pipeline import post_id
    out = []
    for r in rows:
        pid = post_id(r["date"], r["slot"])
        p = db.get(pid) or {}
        item = dict(r, id=pid, status=p.get("status", "scheduled"), permalink=p.get("permalink"),
                    error=p.get("error"), notes=(p.get("report") or {}).get("notes", []) if p else [],
                    shown_topic=p.get("topic") or r["topic"])
        item["thumb"], item["video"] = _thumb(p), _video(p)
        out.append(item)
    return out


def _course(cal):
    from .writer import COURSE
    lessons = []
    for r in _with_status(cal):
        m = COURSE.match(r["topic"])
        if m:
            lessons.append(dict(r, course=m.group(1), n=int(m.group(2)), lesson=m.group(3)))
    courses = {}
    for l in lessons:
        courses.setdefault(l["course"], []).append(l)
    result = []
    for name, ls in courses.items():
        ls.sort(key=lambda l: l["n"])
        done = sum(l["status"] == "published" for l in ls)
        nxt = next((l for l in ls if l["status"] != "published"), None)
        result.append({"name": name, "lessons": ls, "done": done, "total": len(ls), "next": nxt})
    return result


def _perf(lv):
    ins = lv.get("insights", {})
    posts = []
    for p in db.query("status='published'"):
        m = ins.get(p["id"], {})
        reach = m.get("reach") or 0
        posts.append({"id": p["id"], "date": p["date"], "topic": p["topic"], "pillar": p["pillar"],
                      "format": p["format"], "permalink": p["permalink"], "reach": reach,
                      "views": m.get("views") or 0, "likes": m.get("likes") or 0,
                      "comments": m.get("comments") or 0, "saved": m.get("saved") or 0,
                      "shares": m.get("shares") or 0})
    by_pillar = {}
    for p in posts:
        by_pillar.setdefault(p["pillar"], []).append(p["reach"])
    pillars = sorted(({"pillar": PILLARS.get(k, (k,))[0], "avg": round(sum(v) / len(v)), "n": len(v)}
                      for k, v in by_pillar.items()), key=lambda x: -x["avg"])
    totals = {k: sum(p[k] for p in posts) for k in ("reach", "views", "likes", "saved", "shares", "comments")}
    return posts, pillars, totals


def bar_chart(items, unit=""):
    """Horizontal single-series bar chart as inline SVG. items: [(label, value)]. One hue, so no legend;
    the section title names the measure. Hover shows the exact value; the table below is the text view."""
    if not items:
        return Markup('<p class="muted">Abhi data nahi. Post publish hone ke baad numbers yahan aayenge.</p>')
    w, label_w, row, bar_h = 560, 220, 36, 18
    top = max(v for _, v in items) or 1
    h = row * len(items) + 10
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" width="100%" role="img">']
    for i, (label, v) in enumerate(items):
        y = 5 + i * row
        bw = max(4, (w - label_w - 70) * v / top)
        short = (label[:24] + "…") if len(label) > 25 else label
        esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
        parts.append(
            f'<g class="bar"><title>{esc(label)}: {v:,}{unit}</title>'
            f'<rect x="0" y="{y}" width="{w}" height="{row - 4}" fill="transparent"/>'
            f'<text x="{label_w - 12}" y="{y + bar_h}" text-anchor="end" class="lbl">{esc(short)}</text>'
            f'<rect x="{label_w}" y="{y + 6}" width="{bw:.1f}" height="{bar_h}" rx="4" class="fill"/>'
            f'<text x="{label_w + bw + 8:.1f}" y="{y + bar_h}" class="val">{v:,}</text></g>')
    parts.append("</svg>")
    return Markup("".join(parts))


def _log_tail(n=10):
    f = S.data_dir / "bot.log"
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]
    keep = [l for l in lines if (" WARNING " in l or " ERROR " in l) and "phonemizer" not in l]
    return keep[-n:]


def page(msg=None):
    lv = live()
    cal = _calendar()
    t = today()
    window = [r for r in cal if str(t) <= r["date"] <= str(t + timedelta(days=3))]
    upcoming = [r for r in cal if r["date"] >= str(t)][:60]
    posts, pillars, totals = _perf(lv)
    quota = lv.get("quota") or [None, None]
    return _env.get_template("dashboard.html.j2").render(
        S=S, msg=msg, token=TOKEN, now=now().strftime("%d %b %Y, %H:%M"), live_at=lv.get("at"),
        public_url=public_url(), password_on=password_on(),
        paused=db.paused(), account=lv.get("account") or {}, quota=quota, profile=profile_url(),
        days=_group_by_day(_with_status(window)), calendar=_with_status(upcoming), courses=_course(cal),
        perf=posts, totals=totals, pillars=pillars,
        reach_chart=bar_chart([(p["topic"], p["reach"]) for p in sorted(posts, key=lambda p: -p["reach"])[:12]]),
        pillar_chart=bar_chart([(p["pillar"], p["avg"]) for p in pillars]),
        failed=db.query("status IN ('failed_check','publish_failed') AND date >= ?", (str(t - timedelta(days=7)),)),
        log_lines=_log_tail(), pillar_names={k: v[0] for k, v in PILLARS.items()})


def _group_by_day(items):
    days = {}
    for it in items:
        days.setdefault(it["date"], []).append(it)
    return list(days.items())


# ---------------------------------------------------------------- login
SESSION_DAYS = 7
MAX_FAILS, LOCK_MIN = 5, 15
_sessions = {}            # session id -> expiry (epoch seconds); a restart logs everyone out
_fails = {}               # client ip -> [failure times]
_auth_lock = threading.Lock()


def password_on():
    """Login is only enforced with a real password; a short or empty one means 'this PC only'."""
    return len(S.dashboard_password) >= 10


def _check_login(user, pw):
    ok_user = secrets.compare_digest(user.encode(), S.dashboard_user.encode())
    ok_pw = secrets.compare_digest(pw.encode(), S.dashboard_password.encode())
    return ok_user and ok_pw


def _locked(ip):
    cutoff = time.time() - LOCK_MIN * 60
    with _auth_lock:
        _fails[ip] = [t for t in _fails.get(ip, []) if t > cutoff]
        return len(_fails[ip]) >= MAX_FAILS


def _fail(ip):
    with _auth_lock:
        _fails.setdefault(ip, []).append(time.time())


def _new_session():
    sid = secrets.token_urlsafe(32)
    with _auth_lock:
        now_t = time.time()
        for k in [k for k, exp in _sessions.items() if exp < now_t]:
            _sessions.pop(k, None)
        _sessions[sid] = now_t + SESSION_DAYS * 86400
    return sid


def _valid(sid):
    with _auth_lock:
        exp = _sessions.get(sid or "")
        return bool(exp and exp > time.time())


LOGIN_PAGE = _env.from_string("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ByteBaithak · Login</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0B1020;color:#F8FAFC;
font:16px/1.5 "Segoe UI",system-ui,sans-serif}form{background:#121A33;border:1px solid #2A3658;border-radius:16px;
padding:28px;width:min(360px,90vw);display:flex;flex-direction:column;gap:12px}h1{margin:0 0 6px;font-size:22px}
h1 span{color:#22D3EE}input{background:#0B1020;border:1px solid #2A3658;border-radius:10px;color:#F8FAFC;
padding:11px 12px;font:inherit}input:focus{outline:2px solid #22D3EE;border-color:transparent}
button{background:#22D3EE;color:#0B1020;border:0;border-radius:10px;padding:11px;font:inherit;font-weight:700;
cursor:pointer}.err{color:#F87171;font-size:14px}.muted{color:#94A3B8;font-size:13px}</style></head><body>
<form method="post" action="/login"><h1>Byte<span>Baithak</span> Dashboard</h1>
{% if err %}<div class="err">{{ err }}</div>{% endif %}
<label class="muted" for="u">Username</label><input id="u" name="user" autocomplete="username" required autofocus>
<label class="muted" for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Login</button></form></body></html>""")

NO_PASSWORD_PAGE = ("<h1>Dashboard bahar se band hai</h1><p>.env mein DASHBOARD_PASSWORD (kam az kam 10 harf) "
                    "set karein, phir worker restart karein.</p>")


# ---------------------------------------------------------------- public link (Cloudflare quick tunnel)
def public_url():
    return db.kv_get("public_url")


def _tunnel_watch():
    """Ask cloudflared for its trycloudflare.com hostname; tell the owner on Discord when it changes."""
    import requests
    while True:
        try:
            host = requests.get(f"{S.tunnel_url}/quicktunnel", timeout=5).json().get("hostname")
            if host:
                url = f"https://{host}"
                if url != public_url():
                    db.kv_set("public_url", url)
                    log.info("dashboard public link: %s", url)
                    if password_on():
                        approval.send(f"🌐 Dashboard ka naya link (login ke saath):\n{url}")
                    else:
                        approval.send("🌐 Dashboard tunnel tayyar hai, lekin .env mein DASHBOARD_PASSWORD set nahi, "
                                      "is liye bahar se band hai.")
        except Exception as e:  # tunnel container not running or still starting
            log.debug("tunnel not ready: %s", e)
        time.sleep(60)


# ---------------------------------------------------------------- web server
ACTIONS = {"ok": "ok:{id}", "no": "no:{id}", "retry": "/retry {id}", "pause": "/pause", "resume": "/resume"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the bot log clean
        pass

    # Requests through Cloudflare always carry Cf-Connecting-Ip (Cloudflare sets it; a visitor cannot remove it).
    def _public(self):
        return bool(self.headers.get("Cf-Connecting-Ip"))

    def _ip(self):
        return self.headers.get("Cf-Connecting-Ip") or self.client_address[0]

    def _host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0].lower()
        return host in ALLOWED_HOSTS or (self._public() and host.endswith(".trycloudflare.com"))

    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _authed(self):
        """True = may see the dashboard. Without a password only this PC (not the tunnel) gets in."""
        if not password_on():
            return not self._public()
        return _valid(self._cookie("bb_session"))

    def _redirect(self, where, cookie=None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store" if ctype.startswith("text/html") else "private, max-age=300")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in parse_qs(self.rfile.read(min(n, 4096)).decode()).items()}

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden")
        url = urlparse(self.path)
        if url.path == "/login":
            if not password_on():
                return self._send(403, NO_PASSWORD_PAGE) if self._public() else self._redirect("/")
            return self._send(200, LOGIN_PAGE.render(err=None))
        if url.path == "/logout":
            with _auth_lock:
                _sessions.pop(self._cookie("bb_session") or "", None)
            return self._redirect("/login", "bb_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        if not self._authed():
            if self._public() and not password_on():
                return self._send(403, NO_PASSWORD_PAGE)
            return self._redirect("/login")
        if url.path == "/":
            msg = parse_qs(url.query).get("msg", [None])[0]
            try:
                return self._send(200, page(msg))
            except Exception as e:
                log.exception("dashboard page failed")
                return self._send(500, f"dashboard error: {e}", "text/plain; charset=utf-8")
        if url.path.startswith("/media/"):
            return self._media(url.path[len("/media/"):])
        return self._send(404, "not found", "text/plain")

    def _media(self, rel):
        base = S.output_dir.resolve()
        f = (base / rel).resolve()
        if base not in f.parents or not f.is_file() or f.suffix.lower() not in (".jpg", ".mp4"):
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return self._send(200, f.read_bytes(), ctype, {"Cache-Control": "max-age=300"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._host_ok() or path not in ("/action", "/login"):
            return self._send(403, "forbidden")
        if path == "/login":
            return self._login()
        if not self._authed():
            return self._redirect("/login")
        form = self._form()
        if not secrets.compare_digest(form.get("token", ""), TOKEN) or form.get("do") not in ACTIONS:
            return self._send(403, "forbidden (reload the page)")
        cmd = ACTIONS[form["do"]].format(id=form.get("id", ""))
        reply = approval.handle(cmd)
        approval.send(f"🖥 Dashboard se: {reply}")   # keep Discord in the loop
        self._redirect("/?msg=" + quote(reply[:300]))

    def _login(self):
        if not password_on():
            return self._send(403, NO_PASSWORD_PAGE)
        ip = self._ip()
        if _locked(ip):
            log.warning("dashboard login locked for %s", ip)
            return self._send(429, LOGIN_PAGE.render(err=f"Bohat ghalat koshishen. {LOCK_MIN} minute baad try karein."))
        form = self._form()
        if not _check_login(form.get("user", ""), form.get("password", "")):
            _fail(ip)
            log.warning("dashboard login failed from %s", ip)
            return self._send(401, LOGIN_PAGE.render(err="Username ya password ghalat hai."))
        secure = "; Secure" if self._public() else ""   # https through Cloudflare, plain http on this PC
        cookie = f"bb_session={_new_session()}; Max-Age={SESSION_DAYS * 86400}; Path=/; HttpOnly; SameSite=Lax{secure}"
        log.info("dashboard login from %s", ip)
        return self._redirect("/", cookie)


def start():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=srv.serve_forever, name="dashboard", daemon=True).start()
    threading.Thread(target=_refresher, name="dashboard-refresh", daemon=True).start()
    threading.Thread(target=_tunnel_watch, name="dashboard-tunnel", daemon=True).start()
    log.info("dashboard on http://localhost:8787 (container port %d)", PORT)
    return srv
