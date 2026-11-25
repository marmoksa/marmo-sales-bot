"""تجميع التطبيق: القاعدة والمحرك والمرسل والمجدول واستقبال الطلبات وحلقة تيليجرام."""
from __future__ import annotations

import hmac
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from .config import Settings
from .db import Database
from .engine import Engine
from .odoo_read import OdooReader
from .resources import Resources
from .scheduler import Scheduler
from .telegram import Deliverer, TelegramClient, parse_update

log = logging.getLogger(__name__)
MAX_BODY = 1_000_000


class App:
    def __init__(self, settings: Settings, http: httpx.Client | None = None, clock=None):
        self.s = settings
        self.res = Resources.load()
        self.db = Database(settings.db_path, settings.tz, clock)
        self.db.init()
        for uid in settings.manager_ids:
            if not self.db.get_user(uid):
                self.db.upsert_user(uid, "المدير", "manager")
        self.odoo = OdooReader(settings)
        self.tg = TelegramClient(settings, http)
        self.engine = Engine(settings, self.db, self.res, self.odoo, fetch_media=self.tg.download)
        self.deliverer = Deliverer(self.tg)
        self.scheduler = Scheduler(settings, self.db, self.res, self.odoo, self.engine.svc, self.deliverer)

    # ---- تحديث واحد من تيليجرام ----
    def handle_update(self, u: dict):
        inb, cb_id, cmd = parse_update(u)
        if cb_id:
            self.tg.answer_callback(cb_id)
        if cmd and cmd[0] == "myid":
            from .messages import text
            self.deliverer.deliver([text(cmd[1], f"معرّفك: {cmd[1]}")])
            return
        if not inb or self.db.seen_before(inb.msg_id):
            return
        self.deliverer.deliver(self.engine.handle(inb))

    def poll_forever(self, stop: threading.Event):
        offset = int(self.db.meta_get("tg_offset", 0) or 0)
        while not stop.is_set():
            try:
                updates = self.tg.get_updates(offset)
            except Exception as e:  # noqa: BLE001
                log.warning("تعذر جلب التحديثات: %s", self.tg.redact(e))
                stop.wait(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self.handle_update(u)
                except Exception:  # noqa: BLE001
                    log.exception("خطأ في معالجة تحديث")
                self.db.meta_set("tg_offset", offset)
            if self.s.dry_run:
                stop.wait(1)

    # ---- استقبال طلب من بوت العميل ----
    def ingest(self, payload: dict):
        lead_id, is_new, outs = self.engine.svc.ingest(payload)
        self.deliverer.deliver(outs)
        return lead_id, is_new


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug("http: " + fmt, *args)

        def _send(self, code, body="", ctype="text/plain; charset=utf-8"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, "ok") if self.path.partition("?")[0] == "/health" else self._send(404, "not found")

        def do_POST(self):
            # نقرأ الجسم أولاً دائماً، وإلا قد يرى العميل connection reset بدل الرد
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if 0 < n <= MAX_BODY else b""
            if self.path.partition("?")[0] != "/api/leads":
                return self._send(404, "not found")
            auth = self.headers.get("Authorization", "")
            tok = auth[7:] if auth.startswith("Bearer ") else ""
            if not app.s.ingest_token or not hmac.compare_digest(tok, app.s.ingest_token):
                return self._send(401, "unauthorized")
            try:
                lead_id, is_new = app.ingest(json.loads(body))
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False), "application/json")
            self._send(200, json.dumps({"id": lead_id, "new": is_new}), "application/json")

    return H


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6      # الشبكة الداخلية في Fly تعمل بـ IPv6، والمنفذ يقبل IPv4 أيضاً


def make_server(app: App, host: str = "::", port: int | None = None) -> ThreadingHTTPServer:
    port = app.s.port if port is None else port
    try:
        return _V6Server((host, port), make_handler(app))
    except OSError:
        return ThreadingHTTPServer(("0.0.0.0" if host == "::" else host, port), make_handler(app))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # يمنع ظهور التوكن في السجل
    settings = Settings.from_env()
    settings.validate_for_serving()
    app = App(settings)
    stop = threading.Event()
    threading.Thread(target=app.scheduler.run_forever, args=(stop,), daemon=True).start()
    threading.Thread(target=app.poll_forever, args=(stop,), daemon=True).start()
    if not settings.manager_ids:
        log.warning("MANAGER_IDS غير مضبوط: أرسل /myid للبوت لتعرف معرّفك، ثم اضبطه بـ fly secrets set")
    if not settings.ingest_token:
        log.warning("INGEST_TOKEN غير مضبوط: استقبال الطلبات من بوت العميل مقفول")
    srv = make_server(app)
    log.info("بوت مبيعات مارمو يعمل. استقبال الطلبات على المنفذ %s (داخلي)", settings.port)
    try:
        srv.serve_forever()
    finally:
        stop.set()
