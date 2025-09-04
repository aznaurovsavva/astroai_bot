# src/storage.py
import os, json, sqlite3
from datetime import datetime
from typing import Optional, Dict, Any

DB_PATH = os.getenv("DB_PATH", "data.sqlite3")

def _conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = _conn()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS profiles(
        user_id    INTEGER PRIMARY KEY,
        full_name  TEXT,
        username   TEXT,
        lang       TEXT,
        created_at TEXT,
        last_seen  TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER,
        payload       TEXT,
        amount_stars  INTEGER,
        status        TEXT,
        charge_id     TEXT,
        meta_json     TEXT,
        created_at    TEXT,
        updated_at    TEXT
    )""")
    con.commit()
    con.close()

def upsert_profile(user_id: int, full_name: str = "", username: str = "", lang: str = ""):
    now = datetime.utcnow().isoformat()
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT user_id FROM profiles WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row:
        cur.execute("""UPDATE profiles
                       SET full_name=?, username=?, lang=?, last_seen=?
                       WHERE user_id=?""",
                    (full_name, username, lang, now, user_id))
    else:
        cur.execute("""INSERT INTO profiles(user_id, full_name, username, lang, created_at, last_seen)
                       VALUES(?,?,?,?,?,?)""",
                    (user_id, full_name, username, lang, now, now))
    con.commit()
    con.close()

def create_order(user_id: int, payload: str, amount_stars: int,
                 status: str = "awaiting_input", charge_id: Optional[str] = None,
                 meta: Optional[Dict[str, Any]] = None) -> int:
    now = datetime.utcnow().isoformat()
    con = _conn()
    cur = con.cursor()
    cur.execute("""INSERT INTO orders(user_id, payload, amount_stars, status, charge_id, meta_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (user_id, payload, amount_stars, status, charge_id,
                 json.dumps(meta or {}, ensure_ascii=False),
                 now, now))
    order_id = cur.lastrowid
    con.commit()
    con.close()
    return order_id

def update_order(order_id: int, *, status: Optional[str] = None,
                 meta_merge: Optional[Dict[str, Any]] = None, charge_id: Optional[str] = None):
    con = _conn()
    cur = con.cursor()
    # fetch current meta
    cur.execute("SELECT meta_json FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    meta = {} if not row or not row[0] else json.loads(row[0])
    if meta_merge:
        meta.update(meta_merge)
    sets, params = [], []
    if status is not None:
        sets += ["status=?"]; params += [status]
    if charge_id is not None:
        sets += ["charge_id=?"]; params += [charge_id]
    sets += ["meta_json=?", "updated_at=?"]
    params += [json.dumps(meta, ensure_ascii=False), datetime.utcnow().isoformat()]
    params += [order_id]
    cur.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", params)
    con.commit()
    con.close()