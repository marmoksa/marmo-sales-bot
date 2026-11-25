import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from marmo_sales.app import App, make_server
from marmo_sales.config import Settings
from marmo_sales.simulate import seed_demo
from marmo_sales.telegram import parse_update, render
from tests.harness import MGR, REP1, REP2, H, ids, stock_xlsx, texts

TOKEN = "123456:SECRETTOKEN"


def upd(n, **kw):
    return {"update_id": n, **kw}


def private(text=None, uid=2001, **extra):
    m = {"from": {"id": uid, "first_name": "أحمد"}, "chat": {"id": uid, "type": "private"}, **extra}
    if text is not None:
        m["text"] = text
    return m


class Parsing(unittest.TestCase):
    def test_text_commands_callbacks_documents_and_groups(self):
        inb, cb, cmd = parse_update(upd(1, message=private("بيانكو")))
        self.assertEqual((inb.kind, inb.text, inb.uid, inb.name, inb.msg_id), ("text", "بيانكو", "2001", "أحمد", "upd:1"))
        self.assertEqual(parse_update(upd(2, message=private("/myid")))[2], ("myid", "2001"))
        self.assertEqual(parse_update(upd(3, message=private("/start@MarmoSalesBot")))[0].text, "قائمة")
        cq = {"id": "cb1", "from": {"id": 2001, "first_name": "أحمد"}, "data": "l:1:open",
              "message": {"chat": {"id": 2001, "type": "private"}}}
        inb, cb, _ = parse_update(upd(4, callback_query=cq))
        self.assertEqual((inb.kind, inb.reply_id, cb), ("reply", "l:1:open", "cb1"))
        doc = private(document={"file_id": "F1", "file_name": "m.xlsx", "mime_type": "x"})
        inb, *_ = parse_update(upd(5, message=doc))
        self.assertEqual((inb.kind, inb.media_id, inb.filename), ("document", "F1", "m.xlsx"))
        self.assertEqual(parse_update(upd(6, message=private(photo=[{}])))[0].kind, "other")
        group = {"from": {"id": 5}, "chat": {"id": -100, "type": "supergroup"}, "text": "قائمة"}
        self.assertEqual(parse_update(upd(7, message=group)), (None, None, None))
        cq["message"]["chat"]["type"] = "group"
        self.assertIsNone(parse_update(upd(8, callback_query=cq))[0])


class Rendering(unittest.TestCase):
    def test_layouts_and_callback_limits(self):
        h = H()
        h.team()
        h.lead(1)
        menu = h.say(MGR, "قائمة")[0]
        text, markup = render(menu)
        self.assertEqual([len(r) for r in markup["inline_keyboard"]], [2] * 5)
        card = h.tap(REP1, "l:1:open")[0]
        self.assertEqual(len(render(card)[1]["inline_keyboard"]), 1)
        # كل معرّف زر يصدر عن المحرك أقل من 64 بايت
        flows = [h.tap(MGR, "m:brief"), h.tap(REP1, "l:1:more"), h.tap(REP1, "l:1:fu"), h.tap(REP1, "l:1:lost"),
                 h.tap(MGR, "tm:list"), h.tap(MGR, "m:team"), h.tap(MGR, "l:1:assign"), h.say(MGR, "قائمة")]
        for outs in flows:
            for i in ids(outs):
                self.assertLessEqual(len(i.encode()), 64, i)


def make_app(handler, **env):
    e = {"TELEGRAM_TOKEN": TOKEN, "MANAGER_IDS": MGR, "INGEST_TOKEN": "tok",
         "DB_PATH": os.path.join(tempfile.mkdtemp(), "s.db"), **env}
    return App(Settings.from_env(e), http=httpx.Client(transport=httpx.MockTransport(handler)))


class Client(unittest.TestCase):
    def test_send_payloads_protect_content_and_errors_hide_token(self):
        seen = []

        def handler(req: httpx.Request):
            seen.append(req)
            if req.url.path.endswith("/sendDocument"):
                return httpx.Response(200, json={"ok": True, "result": {}})
            if "boom" in req.content.decode(errors="ignore"):
                return httpx.Response(200, json={"ok": False, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        app = make_app(handler)
        from marmo_sales import messages as M
        app.tg.send(M.buttons("2001", "مرحبا", [("a", "أ"), ("b", "ب")]))
        body = seen[-1].content.decode()
        self.assertIn(f"/bot{TOKEN}/sendMessage", str(seen[-1].url))
        self.assertIn("protect_content=true", body)
        self.assertIn("inline_keyboard", __import__("urllib.parse").parse.unquote_plus(body))
        app.tg.send(M.document("2001", b"PK", "تقرير.xlsx", "ملف"))
        self.assertIn(b"multipart/form-data", seen[-1].headers["content-type"].encode())
        with self.assertRaises(RuntimeError) as ctx:
            app.tg.send(M.text("2001", "boom"))
        self.assertNotIn("SECRETTOKEN", str(ctx.exception))
        self.assertIn("chat not found", str(ctx.exception))

    def test_connection_error_hides_token(self):
        def handler(req):
            raise httpx.ConnectError(f"cannot reach {req.url}")
        app = make_app(handler)
        with self.assertRaises(RuntimeError) as ctx:
            app.tg.call("getMe")
        self.assertNotIn("SECRETTOKEN", str(ctx.exception))

    def test_download_file(self):
        def handler(req):
            if req.url.path.endswith("/getFile"):
                return httpx.Response(200, json={"ok": True, "result": {"file_path": "docs/f.xlsx"}})
            return httpx.Response(200, content=b"DATA")
        self.assertEqual(make_app(handler).tg.download("F1"), b"DATA")


class Polling(unittest.TestCase):
    def test_poll_handles_update_once_and_stores_offset(self):
        sent, polls = [], {"n": 0}
        stop = threading.Event()

        def handler(req: httpx.Request):
            m = req.url.path.rsplit("/", 1)[1]
            if m == "getUpdates":
                polls["n"] += 1
                if polls["n"] == 1:
                    return httpx.Response(200, json={"ok": True, "result": [
                        upd(10, message=private("/myid", uid=int(REP1))), upd(10, message=private("/myid", uid=int(REP1)))]})
                stop.set()
                return httpx.Response(200, json={"ok": True, "result": []})
            sent.append(m)
            return httpx.Response(200, json={"ok": True, "result": {}})

        app = make_app(handler)
        app.poll_forever(stop)
        self.assertEqual(app.db.meta_get("tg_offset"), "11")
        self.assertEqual(sent.count("sendMessage"), 2)       # /myid لا يمر بمنع التكرار لأنه أمر بلا حالة

    def test_duplicate_update_is_ignored(self):
        h = H()
        h.team()
        n0 = len(h.app.tg.sent)
        h.app.handle_update(upd(20, message=private("قائمة", uid=int(REP1))))
        h.app.handle_update(upd(20, message=private("قائمة", uid=int(REP1))))
        self.assertEqual(len(h.app.tg.sent) - n0, 1)

    def test_unregistered_user_and_callback_ack(self):
        h = H()
        h.team()
        h.app.handle_update(upd(30, message=private("قائمة", uid=777)))
        self.assertIn("معرّفك: 777", h.app.tg.sent[-1]["text"])
        cq = {"id": "cb9", "from": {"id": int(REP1), "first_name": "أحمد"}, "data": "m:help",
              "message": {"chat": {"id": int(REP1), "type": "private"}}}
        h.app.handle_update(upd(31, callback_query=cq))
        self.assertEqual(h.app.tg.sent[-2]["method"], "answerCallbackQuery")


class IngestServer(unittest.TestCase):
    def setUp(self):
        self.h = H()
        self.h.team()
        self.srv = make_server(self.h.app, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def post(self, payload, token="tok"):
        req = urllib.request.Request(self.base + "/api/leads", data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_health_auth_validation_and_ingest(self):
        with urllib.request.urlopen(self.base + "/health") as r:
            self.assertEqual(r.read(), b"ok")
        lead = {"external_ref": "tg:77", "item_name": "جرانيت Bianco", "customer_name": "محمد", "phone": "0501234567",
                "branch_name": "الرياض"}
        self.assertEqual(self.post(lead, token=None)[0], 401)
        self.assertEqual(self.post(lead, token="bad")[0], 401)
        self.assertEqual(self.post({"external_ref": "x"})[0], 400)
        code, body = self.post(lead)
        self.assertEqual((code, body["new"]), (200, True))
        self.assertEqual(self.post(lead)[1]["new"], False)
        notices = [m for m in self.h.app.tg.sent if m["chat_id"] == REP1]
        self.assertEqual(len(notices), 1)                    # أُرسل للمندوب مرة واحدة فقط
        self.assertIn("محمد", notices[0]["text"])


class Scheduled(unittest.TestCase):
    def test_morning_brief_once_per_workday_only_after_hour(self):
        h = H()
        h.team()
        h.lead(1)
        h.now = h.now.replace(hour=7)
        h.app.scheduler.tick()
        self.assertEqual([m for m in h.app.tg.sent if "لوحة" in m.get("text", "") or "صباح" in m.get("text", "")], [])
        h.now = h.now.replace(hour=8, minute=5)
        h.app.scheduler.tick()
        h.app.scheduler.tick()
        briefs = [m for m in h.app.tg.sent if "صباح الخير" in m.get("text", "") or "لوحة اليوم" in m.get("text", "")]
        self.assertEqual(len(briefs), 3)                     # المدير والمندوبان، مرة واحدة
        h.app.tg.sent.clear()
        h.now = datetime(2026, 12, 4, 8, 30, tzinfo=ZoneInfo("Asia/Riyadh"))      # الجمعة
        h.app.scheduler.tick()
        self.assertEqual(h.app.tg.sent, [])

    def test_uncontacted_alert_only_when_configured_and_once(self):
        h = H(ALERT_UNCONTACTED_HOURS="2", BRIEF_HOUR="20")
        h.team()
        h.lead(1)
        h.advance(hours=1)
        h.app.scheduler.tick()
        self.assertEqual(h.app.tg.sent[-1]["text"].count("بلا تواصل"), 0) if len(h.app.tg.sent) > 1 else None
        h.app.tg.sent.clear()
        h.advance(hours=2)
        h.app.scheduler.tick()
        alerts = [m for m in h.app.tg.sent if "بلا تواصل منذ" in m["text"]]
        self.assertEqual({m["chat_id"] for m in alerts}, {MGR, REP1})
        h.app.tg.sent.clear()
        h.app.scheduler.tick()
        self.assertEqual(h.app.tg.sent, [])
        h2 = H(BRIEF_HOUR="20")
        h2.team()
        h2.lead(1)
        h2.advance(days=5)
        h2.app.tg.sent.clear()
        h2.app.scheduler.tick()
        self.assertEqual(h2.app.tg.sent, [])                 # بلا إعداد لا تنبيه


class Demo(unittest.TestCase):
    def test_demo_seed_gives_working_bot(self):
        h = H(MANAGER_IDS="966500000001")
        seed_demo(h.app)
        t = texts(h.say("966500000002", "عندنا ايه جرانيت 2 سم"))
        self.assertIn("نتائج البحث", t)
        self.assertEqual(len(h.db.leads_of("966500000002")), 2)       # أحمد (الرياض)


if __name__ == "__main__":
    unittest.main()
