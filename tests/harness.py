"""أدوات الاختبار: تطبيق كامل في وضع التجربة، وساعة يتحكم بها الاختبار، وملفات مخزون جاهزة."""
from __future__ import annotations

import io
import os
import tempfile
import threading
from datetime import datetime, timedelta
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer
from zoneinfo import ZoneInfo

from openpyxl import Workbook

from marmo_sales.app import App
from marmo_sales.config import Settings
from marmo_sales.messages import Inbound

MGR, REP1, REP2 = "1001", "2001", "2002"


class H:
    def __init__(self, **env):
        self.tmp = tempfile.mkdtemp()
        self.now = datetime(2026, 11, 29, 9, 0, tzinfo=ZoneInfo("Asia/Riyadh"))   # أحد
        e = {"DRY_RUN": "1", "DB_PATH": os.path.join(self.tmp, "s.db"), "MANAGER_IDS": MGR,
             "INGEST_TOKEN": "tok", **env}
        self.app = App(Settings.from_env(e), clock=lambda: self.now)
        self.db, self.e = self.app.db, self.app.engine
        self.files = {}
        self.e.fetch_media = lambda mid: self.files[mid]
        self.n = 0

    def user(self, uid, name, role="rep", branch="", odoo=None):
        self.db.upsert_user(uid, name, role, branch, odoo)

    def team(self):
        self.user(MGR, "مصطفى", "manager")
        self.user(REP1, "أحمد", "rep", "الرياض", 12)
        self.user(REP2, "سعد", "rep", "الجنوب", 13)

    def say(self, uid, text):
        self.n += 1
        return self.e.handle(Inbound(uid, "text", text=text, msg_id=str(self.n)))

    def tap(self, uid, rid):
        return self.e.handle(Inbound(uid, "reply", reply_id=rid))

    def doc(self, uid, data, filename):
        mid = f"file{len(self.files)}"
        self.files[mid] = data
        return self.e.handle(Inbound(uid, "document", media_id=mid, filename=filename))

    def lead(self, n=1, branch="الرياض", **kw):
        p = {"external_ref": f"t:{n}", "item_name": "جرانيت Bianco بلاط 60×30 2 سم لامع", "customer_name": f"عميل {n}",
             "phone": f"05011122{n:02d}", "branch_name": branch, "quantity": "20 إلى 100 م²",
             "customer_type": "فرد", "request_code": "GR-BIA-TBD-TL-6030-020-POL", **kw}
        return self.app.ingest(p)

    def advance(self, **kw):
        self.now += timedelta(**kw)


def texts(outs) -> str:
    return "\n".join(o.body for o in outs)


def ids(outs) -> list:
    r = []
    for o in outs:
        r += [i for i, *_ in (o.buttons or o.rows)]
    return r


STOCK_HEAD = ["الكود الجديد", "الاسم", "الموقع", "الكمية", "الوحدة", "راكد", "سعر التكلفة"]
STOCK_ROWS = [
    ["GR-BIA-1ST-TL-6030-020-POL-001", "جرانيت Bianco بلاط 60×30 2 سم لامع فرز أول", "1000-FGD", 120, "م²", "", 55.5],
    ["GR-BIA-1ST-TL-6030-020-POL-001", "جرانيت Bianco بلاط 60×30 2 سم لامع فرز أول", "3000-FGD", 40, "م²", "", 55.5],
    ["GR-ROG-1ST-TL-6030-020-POL-001", "جرانيت Royal Gold بلاط 60×30 2 سم لامع فرز أول", "1000-FGD", 80, "م²", "نعم", 60],
    ["GR-TRO-1ST-TL-6030-020-POL-001", "جرانيت Tropical بلاط 60×30 2 سم لامع فرز أول", "2000-SHW", 55, "م²", "", 58],
    ["GR-BIA-1ST-TL-6030-030-POL-001", "جرانيت Bianco بلاط 60×30 3 سم لامع فرز أول", "1000-FGD", 70, "م²", "", 80],
    ["MR-CAR-1ST-SL-STD-020-POL-001", "رخام Carara لوح 2 سم لامع فرز أول", "1000-SLB", 300, "م²", "", 90],
    ["GR-BIA-1ST-TL-6030-020-POL-001", "جرانيت Bianco بلاط 60×30 2 سم لامع فرز أول", "1000-BLK", 999, "م²", "", 1],
    ["GR-BIA-1ST-TL-6030-020-POL-001", "جرانيت Bianco بلاط 60×30 2 سم لامع فرز أول", "4000-BNK1", 999, "م²", "", 1],
    ["GR-BIA-1ST-TL-6030-020-UNP-001", "جرانيت Bianco بلاط 60×30 2 سم غير مجلي فرز أول", "1000-FGD", 0, "م²", "", 1],
]


def stock_xlsx(rows=None, head=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["تقرير مخزون", None])      # صف عنوان قبل صف العناوين
    ws.append(head or STOCK_HEAD)
    for r in rows or STOCK_ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class FakeOdoo:
    """خادم xmlrpc صغير يقلد أودو للقراءة."""

    def __init__(self, partners=None, orders=None, invoices=None):
        self.partners = partners or []
        self.orders = orders or []
        self.invoices = invoices or []
        self.calls = []

        class Hd(SimpleXMLRPCRequestHandler):
            rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")

        self.srv = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=Hd, logRequests=False, allow_none=True)
        self.srv.register_function(lambda db, login, key, x: 7 if key == "KEY" else False, "authenticate")
        self.srv.register_function(self._exec, "execute_kw")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def env(self):
        return {"ODOO_URL": self.url, "ODOO_DB": "x", "ODOO_LOGIN": "u", "ODOO_API_KEY": "KEY"}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _exec(self, db, uid, key, model, method, args, kw=None):
        self.calls.append((model, method, args))
        if model == "res.partner":
            return self.partners
        if model == "sale.order":
            return self.orders
        if model == "account.move" and method == "search_read":
            dom = args[0]
            inv = self.invoices
            for d in dom:
                if isinstance(d, list) and d[0] == "invoice_user_id":
                    inv = [i for i in inv if i["user"] == d[2]]
                if isinstance(d, list) and d[0] == "partner_id":
                    inv = [i for i in inv if i["partner_id"][0] == d[2]]
            return [{k: v for k, v in i.items() if k != "user"} for i in inv]
        if model == "account.move" and method == "read_group":
            return [{"invoice_user_id": [12, "أحمد"], "amount_untaxed_signed": 1500.0},
                    {"invoice_user_id": [13, "سعد"], "amount_untaxed_signed": 900.0}]
        return []
