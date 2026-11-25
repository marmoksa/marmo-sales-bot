"""قاعدة البيانات (SQLite). كل الاستعلامات هنا. التواريخ نصوص ISO بتوقيت السعودية."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid TEXT PRIMARY KEY, name TEXT, role TEXT NOT NULL DEFAULT 'rep', branch TEXT,
    odoo_user_id INTEGER, active INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT '', state_data TEXT NOT NULL DEFAULT '{}',
    last_inbound TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, external_ref TEXT UNIQUE, created_at TEXT NOT NULL,
    item_name TEXT, request_code TEXT, quantity TEXT, customer_type TEXT, project_name TEXT,
    branch_name TEXT, customer_name TEXT, phone TEXT, notes TEXT,
    status TEXT NOT NULL DEFAULT 'new', assigned_to TEXT, assigned_at TEXT,
    first_contact_at TEXT, last_activity_at TEXT, lost_reason TEXT, closed_at TEXT, alerted_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS ix_leads_assigned ON leads(assigned_to);
CREATE INDEX IF NOT EXISTS ix_leads_phone ON leads(phone);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, at TEXT NOT NULL,
    by_user TEXT, kind TEXT NOT NULL, text TEXT
);
CREATE INDEX IF NOT EXISTS ix_act_lead ON activities(lead_id);
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, owner TEXT NOT NULL, due_at TEXT NOT NULL,
    text TEXT, done INTEGER NOT NULL DEFAULT 0, done_at TEXT, reminded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_fu_owner ON followups(owner, done);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, lead_id INTEGER, ref TEXT,
    requested_by TEXT NOT NULL, text TEXT, status TEXT NOT NULL DEFAULT 'pending',
    decided_by TEXT, decided_at TEXT, note TEXT, created_at TEXT NOT NULL, auto INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stock (
    code TEXT, name TEXT, fam TEXT, org TEXT, grd TEXT, shp TEXT, siz TEXT, thk INTEGER, fin TEXT,
    site TEXT, qty REAL, unit TEXT, stagnant INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS seen (msg_id TEXT PRIMARY KEY, at TEXT);
"""

LEAD_FIELDS = ("external_ref", "item_name", "request_code", "quantity", "customer_type", "project_name",
               "branch_name", "customer_name", "phone", "notes")


class Database:
    def __init__(self, path: str, tz, clock=None):
        self.path, self.tz = path, tz
        self._clock = clock

    def now_dt(self) -> datetime:
        return self._clock() if self._clock else datetime.now(self.tz)

    def now(self) -> str:
        return self.now_dt().strftime("%Y-%m-%d %H:%M")

    @contextmanager
    def _c(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 15000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._c() as c:
            c.execute("PRAGMA journal_mode = WAL")
            c.executescript(SCHEMA)

    # ---- عام ----
    def meta_get(self, k, default=None):
        with self._c() as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def meta_set(self, k, v):
        with self._c() as c:
            c.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    def seen_before(self, msg_id: str) -> bool:
        """يسجل المعرّف، ويعيد True إن كان مسجلاً (ميتا قد تعيد إرسال الرسالة)."""
        if not msg_id:
            return False
        with self._c() as c:
            cur = c.execute("INSERT OR IGNORE INTO seen (msg_id, at) VALUES (?, ?)", (msg_id, self.now()))
            return cur.rowcount == 0

    # ---- المستخدمون ----
    def upsert_user(self, uid, name="", role="rep", branch="", odoo_user_id=None, active=1):
        with self._c() as c:
            c.execute(
                "INSERT INTO users (uid, name, role, branch, odoo_user_id, active, created_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET name=excluded.name, role=excluded.role, branch=excluded.branch, "
                "odoo_user_id=excluded.odoo_user_id, active=excluded.active",
                (uid, name, role, branch, odoo_user_id, active, self.now()))

    def get_user(self, uid):
        with self._c() as c:
            return c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()

    def users(self, role=None, active_only=True):
        q, a = "SELECT * FROM users WHERE 1=1", []
        if role:
            q += " AND role=?"; a.append(role)
        if active_only:
            q += " AND active=1"
        with self._c() as c:
            return c.execute(q + " ORDER BY role, name", a).fetchall()

    def set_active(self, uid, active: bool):
        with self._c() as c:
            c.execute("UPDATE users SET active=? WHERE uid=?", (1 if active else 0, uid))

    def touch_inbound(self, uid):
        with self._c() as c:
            c.execute("UPDATE users SET last_inbound=? WHERE uid=?", (self.now(), uid))

    def set_state(self, uid, state="", data=None):
        with self._c() as c:
            c.execute("UPDATE users SET state=?, state_data=? WHERE uid=?",
                      (state, json.dumps(data or {}, ensure_ascii=False), uid))

    def get_state(self, uid):
        u = self.get_user(uid)
        return (u["state"], json.loads(u["state_data"] or "{}")) if u else ("", {})

    # ---- الطلبات ----
    def add_lead(self, d: dict):
        """يعيد (المعرّف، هل هو جديد). التكرار بنفس external_ref يعيد الموجود."""
        ref = d.get("external_ref")
        with self._c() as c:
            if ref:
                r = c.execute("SELECT id FROM leads WHERE external_ref=?", (ref,)).fetchone()
                if r:
                    return r[0], False
            cols = [f for f in LEAD_FIELDS if d.get(f) not in (None, "")]
            created = d.get("created_at") or self.now()
            cur = c.execute(
                f"INSERT INTO leads (created_at, last_activity_at, {', '.join(cols)}) VALUES (?, ?, {', '.join('?' * len(cols))})",
                [created, created] + [d[f] for f in cols])
            return cur.lastrowid, True

    def lead(self, lead_id):
        with self._c() as c:
            return c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()

    def assign(self, lead_id, uid):
        with self._c() as c:
            c.execute("UPDATE leads SET assigned_to=?, assigned_at=?, last_activity_at=? WHERE id=?",
                      (uid, self.now(), self.now(), lead_id))

    def set_status(self, lead_id, status, reason=None):
        now = self.now()
        with self._c() as c:
            l = c.execute("SELECT first_contact_at FROM leads WHERE id=?", (lead_id,)).fetchone()
            first = l["first_contact_at"] or (now if status != "new" else None)
            closed = now if status in ("won", "lost") else None
            c.execute("UPDATE leads SET status=?, first_contact_at=?, last_activity_at=?, lost_reason=?, closed_at=? WHERE id=?",
                      (status, first, now, reason if status == "lost" else None, closed, lead_id))

    def touch_lead(self, lead_id):
        with self._c() as c:
            c.execute("UPDATE leads SET last_activity_at=? WHERE id=?", (self.now(), lead_id))

    def leads_of(self, uid, open_only=True, limit=200):
        q = "SELECT * FROM leads WHERE assigned_to=?"
        if open_only:
            q += " AND status NOT IN ('won','lost')"
        with self._c() as c:
            return c.execute(q + " ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()

    def open_leads(self, limit=500):
        with self._c() as c:
            return c.execute("SELECT * FROM leads WHERE status NOT IN ('won','lost') ORDER BY id LIMIT ?", (limit,)).fetchall()

    def unassigned(self, limit=50):
        with self._c() as c:
            return c.execute("SELECT * FROM leads WHERE assigned_to IS NULL AND status NOT IN ('won','lost') ORDER BY id LIMIT ?",
                             (limit,)).fetchall()

    def uncontacted(self, before: str | None = None):
        q, a = "SELECT * FROM leads WHERE status='new'", []
        if before:
            q += " AND created_at <= ?"; a.append(before)
        with self._c() as c:
            return c.execute(q + " ORDER BY id", a).fetchall()

    def leads_by_phone(self, phone_tail: str, limit=10):
        with self._c() as c:
            return c.execute("SELECT * FROM leads WHERE phone LIKE ? ORDER BY id DESC LIMIT ?",
                             (f"%{phone_tail}", limit)).fetchall()

    def leads_by_name(self, name: str, limit=10):
        with self._c() as c:
            return c.execute("SELECT * FROM leads WHERE customer_name LIKE ? ORDER BY id DESC LIMIT ?",
                             (f"%{name}%", limit)).fetchall()

    def all_leads(self, month_prefix=None):
        with self._c() as c:
            if month_prefix:
                return c.execute("SELECT * FROM leads WHERE created_at LIKE ? ORDER BY id", (month_prefix + "%",)).fetchall()
            return c.execute("SELECT * FROM leads ORDER BY id").fetchall()

    def mark_alerted(self, lead_id):
        with self._c() as c:
            c.execute("UPDATE leads SET alerted_at=? WHERE id=?", (self.now(), lead_id))

    def day_counts(self, day: str):
        with self._c() as c:
            r = c.execute("SELECT COUNT(*) FROM leads WHERE created_at LIKE ?", (day + "%",)).fetchone()
            won = c.execute("SELECT COUNT(*) FROM leads WHERE closed_at LIKE ? AND status='won'", (day + "%",)).fetchone()
        return {"new": r[0], "won": won[0]}

    # ---- الأنشطة ----
    def add_activity(self, lead_id, by_user, kind, text=""):
        with self._c() as c:
            c.execute("INSERT INTO activities (lead_id, at, by_user, kind, text) VALUES (?,?,?,?,?)",
                      (lead_id, self.now(), by_user, kind, text))

    def activities(self, lead_id, n=8):
        with self._c() as c:
            return c.execute("SELECT * FROM activities WHERE lead_id=? ORDER BY id DESC LIMIT ?", (lead_id, n)).fetchall()

    # ---- المتابعات ----
    def add_followup(self, owner, due_at, text="", lead_id=None):
        with self._c() as c:
            return c.execute("INSERT INTO followups (lead_id, owner, due_at, text) VALUES (?,?,?,?)",
                             (lead_id, owner, due_at, text)).lastrowid

    def followup(self, fid):
        with self._c() as c:
            return c.execute("SELECT * FROM followups WHERE id=?", (fid,)).fetchone()

    def followups_of(self, owner, upto=None, limit=20):
        q, a = "SELECT * FROM followups WHERE owner=? AND done=0", [owner]
        if upto:
            q += " AND due_at <= ?"; a.append(upto)
        with self._c() as c:
            return c.execute(q + " ORDER BY due_at LIMIT ?", a + [limit]).fetchall()

    def due_followups(self, now: str):
        with self._c() as c:
            return c.execute("SELECT * FROM followups WHERE done=0 AND reminded=0 AND due_at <= ? ORDER BY due_at", (now,)).fetchall()

    def mark_reminded(self, fid):
        with self._c() as c:
            c.execute("UPDATE followups SET reminded=1 WHERE id=?", (fid,))

    def finish_followup(self, fid):
        with self._c() as c:
            c.execute("UPDATE followups SET done=1, done_at=? WHERE id=?", (self.now(), fid))

    def followup_count(self):
        with self._c() as c:
            return c.execute("SELECT COUNT(*) FROM followups WHERE done=0 AND due_at <= ?", (self.now()[:10] + " 23:59",)).fetchone()[0]

    # ---- الاعتمادات ----
    def add_approval(self, kind, requested_by, text, lead_id=None, ref="", status="pending", decided_by=None, auto=0):
        now = self.now()
        with self._c() as c:
            return c.execute(
                "INSERT INTO approvals (kind, lead_id, ref, requested_by, text, status, decided_by, decided_at, created_at, auto) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (kind, lead_id, ref, requested_by, text, status, decided_by, now if decided_by else None, now, auto)).lastrowid

    def approval(self, aid):
        with self._c() as c:
            return c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()

    def pending_approvals(self):
        with self._c() as c:
            return c.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY id").fetchall()

    def decide(self, aid, status, by, note=""):
        with self._c() as c:
            c.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, note=? WHERE id=? AND status='pending'",
                      (status, by, self.now(), note, aid))

    # ---- المخزون ----
    def replace_stock(self, rows, source, filename, by):
        with self._c() as c:
            c.execute("DELETE FROM stock")
            c.executemany(
                "INSERT INTO stock (code, name, fam, org, grd, shp, siz, thk, fin, site, qty, unit, stagnant) "
                "VALUES (:code,:name,:fam,:org,:grd,:shp,:siz,:thk,:fin,:site,:qty,:unit,:stagnant)", rows)
        self.meta_set("stock_loaded_at", self.now())
        self.meta_set("stock_source", source)
        self.meta_set("stock_file", filename)
        self.meta_set("stock_by", by)
        self.meta_set("stock_rows", len(rows))

    def stock_rows(self):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT * FROM stock")]

    def mark_stagnant(self, codes):
        with self._c() as c:
            c.execute("UPDATE stock SET stagnant=0")
            n = 0
            for code in codes:
                n += c.execute("UPDATE stock SET stagnant=1 WHERE code=?", (code,)).rowcount
        return n
