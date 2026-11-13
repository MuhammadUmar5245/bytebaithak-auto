"""Tiny SQLite store: one row per post, plus key/value state and metrics."""
import json
import sqlite3
import threading
from datetime import timedelta

from .config import S, now

_lock = threading.Lock()
_conn = sqlite3.connect(S.data_dir / "posts.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  date TEXT, slot TEXT, pillar TEXT, format TEXT, topic TEXT,
  status TEXT, content TEXT, report TEXT, media_dir TEXT,
  ig_media_id TEXT, permalink TEXT, error TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS metrics (
  post_id TEXT, taken_at TEXT, hours INTEGER, data TEXT,
  PRIMARY KEY (post_id, hours)
);
"""
)
_conn.commit()

# Status flow:
# drafted -> pending_approval -> approved -> published
#         \-> failed_check        \-> rejected     \-> publish_failed / skipped


def _now():
    return now().isoformat(timespec="seconds")


def upsert(post_id, **fields):
    fields = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
              for k, v in fields.items()}
    fields["updated_at"] = _now()
    with _lock:
        cur = _conn.execute("SELECT id FROM posts WHERE id=?", (post_id,))
        if cur.fetchone():
            sets = ", ".join(f"{k}=?" for k in fields)
            _conn.execute(f"UPDATE posts SET {sets} WHERE id=?", (*fields.values(), post_id))
        else:
            fields["id"] = post_id
            fields["created_at"] = _now()
            cols = ", ".join(fields)
            qs = ", ".join("?" for _ in fields)
            _conn.execute(f"INSERT INTO posts ({cols}) VALUES ({qs})", tuple(fields.values()))
        _conn.commit()


def get(post_id):
    row = _conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    return _decode(row) if row else None


def query(where="1=1", params=()):
    rows = _conn.execute(f"SELECT * FROM posts WHERE {where} ORDER BY date, slot", params).fetchall()
    return [_decode(r) for r in rows]


def recent_topics(limit=120, exclude_id=None):
    rows = _conn.execute(
        "SELECT topic FROM posts WHERE status NOT IN ('rejected','failed_check','drafting') AND id != ? "
        "ORDER BY created_at DESC LIMIT ?", (exclude_id or "", limit)).fetchall()
    return [r["topic"] for r in rows if r["topic"]]


def _decode(row):
    d = dict(row)
    for k in ("content", "report"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def kv_get(k, default=None):
    row = _conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return row["v"] if row else default


def kv_set(k, v):
    with _lock:
        _conn.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (k, str(v)))
        _conn.commit()


def save_metrics(post_id, hours, data):
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO metrics (post_id, taken_at, hours, data) VALUES (?, ?, ?, ?)",
            (post_id, _now(), hours, json.dumps(data)))
        _conn.commit()


def metrics_since(days=7):
    cutoff = (now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = _conn.execute(
        "SELECT m.post_id, m.hours, m.data, p.topic, p.pillar, p.date FROM metrics m "
        "JOIN posts p ON p.id = m.post_id WHERE m.taken_at >= ?", (cutoff,)).fetchall()
    return [dict(r, data=json.loads(r["data"])) for r in rows]


def paused():
    return kv_get("paused", "0") == "1"
