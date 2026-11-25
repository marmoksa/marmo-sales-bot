"""المهام الدورية: تذكير المتابعات، ملخص الصباح، تنبيه الطلبات بلا تواصل، تحديث المخزون من أودو."""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from . import briefs, stock
from . import messages as M

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, settings, db, res, odoo, svc, deliverer):
        self.s, self.db, self.res, self.odoo, self.svc, self.out = settings, db, res, odoo, svc, deliverer

    def tick(self):
        """تُستدعى كل دقيقة. كل مهمة معزولة حتى لا يوقف فشلها الباقي."""
        for task in (self.followups, self.morning_brief, self.uncontacted_alerts, self.refresh_stock):
            try:
                task()
            except Exception:  # noqa: BLE001
                log.exception("فشلت المهمة الدورية %s", task.__name__)

    def followups(self):
        outs = []
        for f in self.db.due_followups(self.db.now()):
            u = self.db.get_user(f["owner"])
            self.db.mark_reminded(f["id"])
            if not u or not u["active"]:
                continue
            who = ""
            if f["lead_id"]:
                l = self.db.lead(f["lead_id"])
                who = l["customer_name"] if l else ""
            btns = [(f"f:{f['id']}:done", "✅ تمت"), (f"f:{f['id']}:snooze", "⏭️ غداً")]
            if f["lead_id"]:
                btns.append((f"l:{f['lead_id']}:open", "📌 الطلب"))
            o = M.buttons(f["owner"], f"⏰ حان موعد المتابعة\n{f['text'] or 'متابعة'}\n{who}", btns)
            outs.append(o)
        self.out.deliver(outs)

    def morning_brief(self):
        now = self.db.now_dt()
        today = now.strftime("%Y-%m-%d")
        if now.weekday() not in self.s.work_days or not (self.s.brief_hour <= now.hour < 12):
            return
        if self.db.meta_get("last_brief") == today:
            return
        self.db.meta_set("last_brief", today)
        outs = []
        for u in self.db.users():
            mgr = u["role"] == "manager"
            body = briefs.manager_dashboard(self.db, now) if mgr else briefs.rep_brief(self.db, u, now)
            line = briefs.manager_summary_line(self.db) if mgr else briefs.rep_summary_line(self.db, u)
            o = M.text(u["uid"], body)
            outs.append(o)
        self.out.deliver(outs)

    def uncontacted_alerts(self):
        hours = self.s.alert_uncontacted_hours
        if not hours:
            return
        cutoff = (self.db.now_dt() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        outs = []
        for l in self.db.uncontacted(before=cutoff):
            if l["alerted_at"]:
                continue
            self.db.mark_alerted(l["id"])
            targets = {m["uid"] for m in self.svc.managers()}
            if l["assigned_to"]:
                targets.add(l["assigned_to"])
            msg = f"الطلب #{l['id']:04d} ({l['customer_name']}) بلا تواصل منذ أكثر من {hours} ساعة"
            for t in targets:
                o = M.buttons(t, "⚠️ " + msg, [(f"l:{l['id']}:open", "📌 فتح الطلب")])
                outs.append(o)
        self.out.deliver(outs)

    def refresh_stock(self):
        if self.s.stock_source != "odoo" or not self.odoo.enabled:
            return
        last = self.db.meta_get("stock_loaded_at")
        if last and self.db.meta_get("stock_source") == "أودو":
            from .views import parse_dt
            if (self.db.now_dt().replace(tzinfo=None) - parse_dt(last)).total_seconds() < 900:
                return
        records = [{**r, "unit": "م²"} for r in self.odoo.stock_rows()]
        rows, rep = stock.normalize_rows(records, self.res.options, self.res.item_map)
        if rows:
            self.db.replace_stock(rows, "أودو", "odoo", "scheduler")

    def run_forever(self, stop: threading.Event):
        while not stop.is_set():
            self.tick()
            stop.wait(60)
