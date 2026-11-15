"""Storage: one SQLite file. JSON blobs where the shape is still moving, rows where we audit."""
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WORK = Path(os.environ.get("LEVEL_WORK_DIR") or DATA)       # state (db, uploads, exports); point at a copy to test without touching a live session
DB_PATH = WORK / "level.db"
SEED = DATA / "seed"
REPLIES = DATA / "vendor_replies"
UPLOADS = WORK / "uploads"
EXPORTS = WORK / "exports"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS responses (
  vendor_id TEXT PRIMARY KEY, email TEXT NOT NULL, dir TEXT NOT NULL, status TEXT NOT NULL,
  extraction TEXT, source_text TEXT, model TEXT, error TEXT, extracted_at REAL);
CREATE TABLE IF NOT EXISTS overrides (
  vendor_id TEXT, line_id TEXT, action TEXT, price REAL, note TEXT, ts REAL, PRIMARY KEY (vendor_id, line_id));
CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, kind TEXT, subject TEXT, body TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS replies (id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, body TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, event TEXT, detail TEXT);
"""


def conn():
    for d in (WORK, UPLOADS, EXPORTS):
        d.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    if "grp" not in {r[1] for r in c.execute("PRAGMA table_info(overrides)")}:
        c.execute("ALTER TABLE overrides ADD COLUMN grp TEXT")            # decisions made for a group of cells share an id, so one undo reverts them
    return c


def get(key, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def put(key, value):
    with conn() as c:
        c.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value, ensure_ascii=False)))


def audit(event, detail=""):
    with conn() as c:
        c.execute("INSERT INTO audit(ts,event,detail) VALUES(?,?,?)", (time.time(), event, detail))


def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def run(sql, args=()):
    with conn() as c:
        c.execute(sql, args)


def many(statements):
    """[(sql, args), ...] in one transaction: all of them or none."""
    with conn() as c:
        for sql, args in statements:
            c.execute(sql, args)


def seed_rfx():
    return json.loads((SEED / "rfx.json").read_text())


DEFAULT_SETTINGS = {"fx": {"USD": 88.5}, "fx_note": "Buyer-set rate", "vendors": {}}


def settings():
    s = get("settings")
    if not s:
        s = json.loads(json.dumps(DEFAULT_SETTINGS))
        seed = SEED / "rfx.json"
        if seed.exists():
            s["fx"] = seed_rfx().get("fx", s["fx"])
        put("settings", s)
    return s


def reset():
    with conn() as c:
        for t in ("kv", "responses", "overrides", "outbox", "replies", "audit"):
            c.execute(f"DELETE FROM {t}")
