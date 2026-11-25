"""محرك المحادثة: يستقبل Inbound ويعيد قائمة Out. لا يعرف شيئاً عن قناة المراسلة.

الحالات المنتظرة للنص تُحفظ في users.state. كل زر يحمل معرّفاً من شكل head:arg:arg.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from . import briefs, stock, views
from . import messages as M
from .messages import Inbound, Out
from .odoo_read import OdooError
from .reports import build_xlsx
from .services import Services, phone_tail

log = logging.getLogger(__name__)
_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
MENU_WORDS = {"قائمه", "القائمه", "menu", "ابدا", "start", "مرحبا", "السلام عليكم", "هلا", "ملخص"}
CANCEL_WORDS = {"الغاء", "إلغاء", "cancel", "الغ"}

REP_MENU = [("m:leads", "📌 طلباتي", "الطلبات المسندة إليك"), ("m:stock", "📦 عندنا إيه؟", "بحث في المخزون"),
            ("m:followups", "⏰ متابعاتي", "المتابعات المستحقة"), ("m:c360", "👤 عميل 360", "بالجوال أو الاسم"),
            ("m:collect", "💰 تحصيلي", "الفواتير المتأخرة لعملائك"), ("m:sales", "📈 مبيعاتي", "مبيعات الشهر"),
            ("m:approve", "✅ طلب اعتماد", "خصم أو ائتمان"), ("m:help", "❔ مساعدة", "كيف أستخدم البوت")]
MGR_MENU = [("m:brief", "📊 لوحة اليوم", "ملخص سريع"), ("m:unassigned", "🆕 غير مسندة", "طلبات تنتظر الإسناد"),
            ("m:approvals", "🔔 الاعتمادات", "طلبات تنتظر قرارك"), ("m:team", "👥 الفريق", "إضافة وإيقاف"),
            ("m:teamleads", "📋 طلبات الفريق", "المفتوح لكل مندوب"), ("m:stock", "📦 عندنا إيه؟", "بحث في المخزون"),
            ("m:c360", "👤 عميل 360", "بالجوال أو الاسم"), ("m:upload", "⬆️ رفع المخزون", "ملف Excel"),
            ("m:report", "📥 تقرير Excel", "كل الطلبات"), ("m:help", "❔ مساعدة", "كيف أستخدم البوت")]

HELP = (
    "طريقة الاستخدام:\n"
    "• اكتب اسم الصنف مباشرة للبحث في المخزون، مثال: بيانكو 60×30 2 سم لامع\n"
    "• اكتب #12 لفتح الطلب رقم 12\n"
    "• اكتب رقم جوال العميل لعرض ملفه\n"
    "• اكتب قائمة في أي وقت للرجوع، وإلغاء لإيقاف أي خطوة\n"
    "• المخزون المعروض من آخر ملف مرفوع، وتاريخه يظهر مع كل نتيجة\n"
    "• لا تظهر هنا تكلفة ولا هامش"
)


class Engine:
    def __init__(self, settings, db, res, odoo, fetch_media=None):
        self.s, self.db, self.res, self.odoo = settings, db, res, odoo
        self.svc = Services(settings, db, res)
        self.fetch_media = fetch_media

    # ===== المدخل =====
    def handle(self, inb: Inbound) -> list:
        wa = inb.uid
        u = self.db.get_user(wa)
        if u is None and wa in self.s.manager_ids:
            self.db.upsert_user(wa, inb.name or "المدير", "manager")
            u = self.db.get_user(wa)
        if u is None or not u["active"]:
            log.warning("رقم غير مسجل: %s", wa)
            return [M.text(wa, f"حسابك غير مسجل في بوت المبيعات.\nمعرّفك: {wa}\nأرسله لمدير المبيعات ليضيفك.")]
        self.db.touch_inbound(wa)
        try:
            if inb.kind == "document":
                return self.on_document(u, inb)
            if inb.kind == "reply":
                return self.on_action(u, inb.reply_id)
            if inb.kind == "other":
                return [M.text(wa, "أستقبل النصوص والأزرار وملفات Excel فقط.")]
            return self.on_text(u, inb.text)
        except OdooError as e:
            return [M.text(wa, f"تعذر الاتصال بأودو الآن: {e}")]

    def now(self) -> datetime:
        return self.db.now_dt()

    def is_mgr(self, u) -> bool:
        return u["role"] == "manager"

    # ===== النصوص =====
    def on_text(self, u, text):
        wa = u["uid"]
        t = " ".join(str(text or "").split())
        n = stock.norm(t)
        if n in {stock.norm(w) for w in MENU_WORDS}:
            self.db.set_state(wa)
            return self.menu(u)
        if n in {stock.norm(w) for w in CANCEL_WORDS}:
            self.db.set_state(wa)
            return [M.text(wa, "تم الإلغاء.")] + self.menu(u)
        state, data = self.db.get_state(wa)
        if state:
            out = self.on_state(u, state, data, t)
            if out is not None:
                return out
        return self.free_text(u, t)

    def free_text(self, u, t):
        tt = t.translate(_AR)
        m = re.fullmatch(r"(?:#|طلب\s*)\s*(\d{1,7})", tt)
        if m:
            return self.show_lead(u, int(m[1]))
        digits = re.sub(r"[\s\-+]", "", tt)
        if digits.isdigit() and len(digits) >= 9:
            return self.customer360(u, tt)
        return self.stock_search(u, t)

    def on_state(self, u, state, data, t):
        wa = u["uid"]
        if state == "stock_results":
            if t.translate(_AR).isdigit() and 1 <= int(t.translate(_AR)) <= len(data.get("codes", [])):
                return self.stock_detail(u, data["codes"][int(t.translate(_AR)) - 1])
            self.db.set_state(wa)
            return None
        if state == "stock_prompt":
            self.db.set_state(wa)
            return self.stock_search(u, t)
        if state == "c360":
            self.db.set_state(wa)
            return self.customer360(u, t)
        if state == "note":
            l, err = self.lead_for(u, data["lead_id"])
            self.db.set_state(wa)
            if err:
                return err
            self.db.add_activity(l["id"], wa, "note", t)
            self.db.touch_lead(l["id"])
            return [M.text(wa, "تم حفظ الملاحظة. ✅")] + self.show_lead(u, l["id"])
        if state == "fu_custom":
            when = self.svc.parse_when(t)
            if not when:
                return [M.text(wa, "الصيغة غير صحيحة. اكتب مثلاً: 2026-12-01 10:00")]
            self.db.set_state(wa, "fu_text", {"lead_id": data.get("lead_id"), "due": when})
            return [M.text(wa, "اكتب سبب المتابعة، أو 0 للتخطي.")]
        if state == "fu_text":
            note = "" if t == "0" else t
            fid = self.db.add_followup(wa, data["due"], note, data.get("lead_id"))
            self.db.set_state(wa)
            if data.get("lead_id"):
                self.db.add_activity(data["lead_id"], wa, "followup", f"متابعة {data['due']} {note}")
            out = [M.text(wa, f"تم ضبط المتابعة رقم {fid} في {data['due']}. ⏰")]
            return out + (self.show_lead(u, data["lead_id"]) if data.get("lead_id") else [])
        if state in ("discount", "credit"):
            self.db.set_state(wa)
            ref = data.get("ref", "")
            body = f"{ref} {t}".strip()
            lead_id = data.get("lead_id")
            if not lead_id:
                m = re.search(r"#(\d{1,7})", t.translate(_AR))
                lead_id = int(m[1]) if m and self.db.lead(int(m[1])) else None
            aid, mine, notice = self.svc.request_approval(state, u, body, lead_id)
            return mine + notice
        if state == "promise":
            when = self.svc.parse_when(t)
            if not when:
                return [M.text(wa, "الصيغة غير صحيحة. اكتب مثلاً: 2026-12-01")]
            self.db.set_state(wa)
            self.db.add_followup(wa, when, f"وعد سداد: {data['name']}")
            self.db.add_activity(None, wa, "collect", f"وعد سداد {when}: {data['name']}")
            return [M.text(wa, f"تم تسجيل وعد السداد وضبط تذكير في {when}. ✅")]
        if state == "team_add":
            return self.team_add(u, t)
        self.db.set_state(wa)
        return None

    # ===== الأزرار =====
    def on_action(self, u, rid):
        p = rid.split(":")
        h = p[0]
        wa = u["uid"]
        try:
            if h == "m":
                return self.menu_action(u, p[1])
            if h == "l":
                return self.lead_action(u, int(p[1]), p[2])
            if h == "lr":
                return self.lost_reason(u, int(p[1]), int(p[2]))
            if h == "fp":
                return self.followup_preset(u, int(p[1]), p[2])
            if h == "f":
                return self.followup_action(u, int(p[1]), p[2])
            if h == "ap":
                return self.approval_action(u, int(p[1]), p[2])
            if h == "as":
                return self.assign_action(u, int(p[1]), p[2])
            if h == "tm":
                return self.team_action(u, p[1], p[2] if len(p) > 2 else "")
            if h == "cl":
                return self.collect_action(u, p[1], p[2] if len(p) > 2 else "open")
            if h == "pg":
                return self.leads_list(u, int(p[1]))
            if h == "ak":
                return self.ask_approval(u, p[1])
        except (ValueError, IndexError):
            pass
        return [M.text(wa, "لم أفهم هذا الاختيار.")] + self.menu(u)

    def menu(self, u):
        rows = MGR_MENU if self.is_mgr(u) else REP_MENU
        name = u["name"] or ""
        return [M.listing(u["uid"], f"أهلاً {name} 👋\nاختر ما تريد، أو اكتب اسم صنف للبحث مباشرة.",
                          [(i, t, "") for i, t, _ in rows], cols=2)]

    def menu_action(self, u, name):
        wa = u["uid"]
        mgr = self.is_mgr(u)
        self.db.set_state(wa)
        if name == "menu":
            return self.menu(u)
        if name == "leads":
            return self.leads_list(u, 1)
        if name == "stock":
            self.db.set_state(wa, "stock_prompt")
            return [M.text(wa, "اكتب الصنف أو مواصفاته.\nمثال: بيانكو 60×30 2 سم لامع\nأو: رخام 3 سم")]
        if name == "followups":
            return self.followups_list(u)
        if name == "c360":
            self.db.set_state(wa, "c360")
            return [M.text(wa, "اكتب رقم جوال العميل أو اسمه:")]
        if name == "collect":
            return self.collect_list(u)
        if name == "sales":
            return self.sales_view(u)
        if name == "approve":
            return [M.buttons(wa, "ما نوع الاعتماد المطلوب؟", [("ak:discount", "💸 خصم"), ("ak:credit", "🏦 ائتمان")])]
        if name == "help":
            return [M.text(wa, HELP)]
        if name == "brief":
            return [M.text(wa, briefs.manager_dashboard(self.db, self.now(), self.sales_lines()) if mgr
                           else briefs.rep_brief(self.db, u, self.now()))]
        if not mgr:
            return [M.text(wa, "هذه الميزة لمدير المبيعات.")]
        if name == "unassigned":
            rows = self.db.unassigned(10)
            if not rows:
                return [M.text(wa, "لا توجد طلبات غير مسندة. 👌")]
            return [M.listing(wa, f"طلبات غير مسندة ({len(rows)})", [views.lead_row(l) for l in rows], "الطلبات")]
        if name == "approvals":
            rows = self.db.pending_approvals()[:10]
            if not rows:
                return [M.text(wa, "لا توجد اعتمادات معلقة. 👌")]
            return [M.listing(wa, f"اعتمادات معلقة ({len(rows)})",
                              [(f"ap:{a['id']}:open", f"#{a['id']} {'خصم' if a['kind'] == 'discount' else 'ائتمان'}",
                                f"{self.svc.name_of(a['requested_by'])}: {a['text']}") for a in rows], "الاعتمادات")]
        if name == "team":
            return [M.buttons(wa, "إدارة الفريق", [("tm:add", "➕ إضافة عضو"), ("tm:list", "👥 الأعضاء")])]
        if name == "teamleads":
            return self.team_leads(u)
        if name == "upload":
            return [M.text(wa, "أرسل ملف Excel للمخزون هنا كمستند.\n"
                               "المطلوب: عمود للكود أو الاسم، وعمود للكمية، ويُفضل عمود للموقع مثل 1000-FGD.\n"
                               "لتحديد الراكد: أرسل ملفاً اسمه يحتوي كلمة راكد وفيه أكواد الأصناف.\n"
                               "الأعمدة الأخرى (التكلفة وغيرها) تُهمل ولا تُخزَّن.")]
        if name == "report":
            leads = self.db.all_leads()
            buf = build_xlsx(leads, "كل الفترات", self.svc.name_of, self.res.options["branches"],
                             [x["name"] for x in self.db.users(role="rep")], self.s.tz)
            return [M.document(wa, buf.read(), "تقرير مبيعات مارمو.xlsx", f"{len(leads)} طلب")]
        return self.menu(u)

    # ===== الطلبات =====
    def lead_for(self, u, lead_id):
        l = self.db.lead(lead_id)
        if not l:
            return None, [M.text(u["uid"], "الطلب غير موجود.")]
        if not self.is_mgr(u) and l["assigned_to"] != u["uid"]:
            owner = self.svc.name_of(l["assigned_to"]) or "غير مسند"
            return None, [M.text(u["uid"], f"هذا الطلب مسند إلى {owner}.")]
        return l, None

    def show_lead(self, u, lead_id):
        l, err = self.lead_for(u, lead_id)
        return err or [views.lead_card(u["uid"], l, self.svc.name_of(l["assigned_to"]), self.now())]

    def leads_list(self, u, page):
        wa = u["uid"]
        rows = self.db.leads_of(wa)
        if not rows:
            return [M.text(wa, "لا توجد طلبات مفتوحة لك. 👌")]
        per = 9
        chunk = rows[(page - 1) * per: page * per]
        items = [views.lead_row(l) for l in chunk]
        if len(rows) > page * per:
            items.append((f"pg:{page + 1}", "التالي ⬅️", f"{len(rows) - page * per} طلب آخر"))
        return [M.listing(wa, f"طلباتك المفتوحة ({len(rows)})", items, "الطلبات")]

    def lead_action(self, u, lead_id, act):
        wa = u["uid"]
        l, err = self.lead_for(u, lead_id)
        if err:
            if act == "assign" and self.is_mgr(u) and self.db.lead(lead_id):
                pass
            else:
                return err
        l = self.db.lead(lead_id)
        if act == "open":
            return self.show_lead(u, lead_id)
        if act in ("contacted", "quoted", "won", "reopen"):
            new = "contacted" if act == "reopen" else act
            self.db.set_status(lead_id, new)
            self.db.add_activity(lead_id, wa, "status", views.STATUS_PLAIN[new])
            return self.show_lead(u, lead_id)
        if act == "lost":
            reasons = self.res.options["lost_reasons"]
            return [M.listing(wa, "ما سبب عدم اكتمال الطلب؟",
                              [(f"lr:{lead_id}:{i}", r, "") for i, r in enumerate(reasons)], "السبب")]
        if act == "fu":
            return [M.listing(wa, "متى نتابع؟", [(f"fp:{lead_id}:{p['id']}", p["label"], "") for p in self.res.options["followup_presets"]], "الوقت")]
        if act == "more":
            rows = [(f"l:{lead_id}:note", "📝 إضافة ملاحظة", ""), (f"l:{lead_id}:won", "✅ تم البيع", ""),
                    (f"l:{lead_id}:lost", "❌ لم يكتمل", ""), (f"l:{lead_id}:discount", "💸 اطلب خصم", "يذهب لمدير المبيعات"),
                    (f"l:{lead_id}:call", "📞 رقم العميل", ""), (f"l:{lead_id}:hist", "🕘 سجل النشاط", ""),
                    (f"l:{lead_id}:c360", "👤 ملف العميل", "")]
            if self.is_mgr(u):
                rows.insert(4, (f"l:{lead_id}:assign", "👤 إسناد أو تحويل", ""))
            return [M.listing(wa, f"خيارات الطلب #{lead_id:04d}", rows, "الخيارات")]
        if act == "note":
            self.db.set_state(wa, "note", {"lead_id": lead_id})
            return [M.text(wa, "اكتب الملاحظة:")]
        if act == "discount":
            self.db.set_state(wa, "discount", {"lead_id": lead_id, "ref": f"#{lead_id:04d}"})
            return [M.text(wa, "اكتب نسبة أو قيمة الخصم والسبب في رسالة واحدة.\nمثال: 5% لأن العميل مشروع كبير")]
        if act == "call":
            return [M.text(wa, f"{l['customer_name']}\n{l['phone']}")]
        if act == "hist":
            acts = self.db.activities(lead_id)
            body = "\n".join(f"{a['at'][5:]} | {self.svc.name_of(a['by_user']) or a['by_user']} | {a['kind']}: {a['text']}" for a in acts)
            return [M.text(wa, f"🕘 سجل الطلب #{lead_id:04d}\n{body or 'لا يوجد نشاط.'}")]
        if act == "c360":
            return self.customer360(u, l["phone"])
        if act == "assign":
            if not self.is_mgr(u):
                return [M.text(wa, "الإسناد لمدير المبيعات.")]
            reps = self.db.users(role="rep")
            if not reps:
                return [M.text(wa, "لا يوجد مندوبون مسجلون. أضفهم من قائمة الفريق.")]
            return [M.listing(wa, f"إسناد الطلب #{lead_id:04d} إلى:",
                              [(f"as:{lead_id}:{r['uid']}", r["name"] or r["uid"], r["branch"] or "كل الفروع") for r in reps], "المندوبون")]
        return self.show_lead(u, lead_id)

    def lost_reason(self, u, lead_id, idx):
        l, err = self.lead_for(u, lead_id)
        if err:
            return err
        reason = self.res.options["lost_reasons"][idx]
        self.db.set_status(lead_id, "lost", reason)
        self.db.add_activity(lead_id, u["uid"], "status", f"لم يكتمل: {reason}")
        return self.show_lead(u, lead_id)

    def assign_action(self, u, lead_id, rep_wa):
        if not self.is_mgr(u):
            return [M.text(u["uid"], "الإسناد لمدير المبيعات.")]
        if not self.db.lead(lead_id) or not self.db.get_user(rep_wa):
            return [M.text(u["uid"], "الطلب أو المندوب غير موجود.")]
        outs = self.svc.assign(lead_id, rep_wa, u["uid"])
        return [M.text(u["uid"], f"تم إسناد الطلب #{lead_id:04d} إلى {self.svc.name_of(rep_wa)}. ✅")] + outs

    # ===== المتابعات =====
    def followup_preset(self, u, lead_id, preset):
        wa = u["uid"]
        lid = lead_id or None
        if lid and self.lead_for(u, lid)[1]:
            return self.lead_for(u, lid)[1]
        if preset == "custom":
            self.db.set_state(wa, "fu_custom", {"lead_id": lid})
            return [M.text(wa, "اكتب التاريخ والوقت بالشكل: 2026-12-01 10:00")]
        due = self.svc.due_from_preset(preset)
        if not due:
            return [M.text(wa, "خيار غير معروف.")]
        self.db.set_state(wa, "fu_text", {"lead_id": lid, "due": due})
        return [M.text(wa, f"موعد المتابعة {due}.\nاكتب سبب المتابعة، أو 0 للتخطي.")]

    def followups_list(self, u):
        wa = u["uid"]
        rows = self.db.followups_of(wa, limit=10)
        if not rows:
            return [M.text(wa, "لا توجد متابعات مفتوحة. 👌")]
        items = []
        for f in rows:
            who = ""
            if f["lead_id"]:
                l = self.db.lead(f["lead_id"])
                who = l["customer_name"] if l else ""
            items.append((f"f:{f['id']}:open", f"{f['due_at'][5:]} {f['text'] or 'متابعة'}", who))
        return [M.listing(wa, f"متابعاتك ({len(rows)})", items, "المتابعات")]

    def followup_action(self, u, fid, act):
        wa = u["uid"]
        f = self.db.followup(fid)
        if not f or (f["owner"] != wa and not self.is_mgr(u)):
            return [M.text(wa, "المتابعة غير موجودة.")]
        if act == "open":
            btns = [(f"f:{fid}:done", "✅ تمت"), (f"f:{fid}:snooze", "⏭️ غداً")]
            if f["lead_id"]:
                btns.append((f"l:{f['lead_id']}:open", "📌 الطلب"))
            return [M.buttons(wa, f"⏰ {f['due_at']}\n{f['text'] or 'متابعة'}", btns)]
        if act == "done":
            self.db.finish_followup(fid)
            return [M.text(wa, "تم إغلاق المتابعة. ✅")]
        if act == "snooze":
            self.db.finish_followup(fid)
            due = self.svc.due_from_preset("tom10")
            self.db.add_followup(f["owner"], due, f["text"], f["lead_id"])
            return [M.text(wa, f"تم التأجيل إلى {due}. ⏭️")]
        return [M.text(wa, "خيار غير معروف.")]

    # ===== الاعتمادات =====
    def ask_approval(self, u, kind):
        wa = u["uid"]
        self.db.set_state(wa, kind, {})
        if kind == "discount":
            return [M.text(wa, "اكتب رقم الطلب (مثل #12) إن وُجد، ثم نسبة أو قيمة الخصم والسبب.")]
        return [M.text(wa, "اكتب اسم العميل والمبلغ المطلوب كحد ائتماني والسبب.")]

    def approval_action(self, u, aid, act):
        wa = u["uid"]
        if not self.is_mgr(u):
            return [M.text(wa, "الاعتماد لمدير المبيعات.")]
        a = self.db.approval(aid)
        if not a:
            return [M.text(wa, "طلب الاعتماد غير موجود.")]
        if act == "open":
            who = self.svc.name_of(a["requested_by"])
            return [M.buttons(wa, f"🔔 {'خصم' if a['kind'] == 'discount' else 'ائتمان'} رقم {aid}\nمن: {who}\n{a['text']}\nالحالة: {a['status']}",
                              [(f"ap:{aid}:yes", "✅ اعتماد"), (f"ap:{aid}:no", "❌ رفض")])]
        return self.svc.decide(aid, act == "yes", u)

    # ===== المخزون =====
    def stock_header(self) -> str:
        at = self.db.meta_get("stock_loaded_at")
        if not at:
            return ""
        src = self.db.meta_get("stock_source", "ملف")
        txt = f"آخر تحديث للمخزون: {at} ({src})"
        age_h = (self.now().replace(tzinfo=None) - datetime.strptime(at, "%Y-%m-%d %H:%M")).total_seconds() / 3600
        return txt + ("\n⚠️ البيانات قديمة، تأكد قبل الوعد للعميل." if age_h > self.s.stock_stale_hours else "")

    def stock_search(self, u, text):
        wa = u["uid"]
        rows = self.db.stock_rows()
        if not rows:
            return [M.text(wa, "لم يُرفع مخزون بعد. اطلب من مدير المبيعات رفع ملف المخزون.")]
        hits, note = stock.search(rows, text, self.res.catalog)
        if note == "empty":
            return [M.text(wa, "اكتب اسم الصنف أو مقاسه للبحث.")]
        if not hits:
            return [M.text(wa, "لم أجد صنفاً بهذه المواصفات. جرّب الاسم التجاري أو مقاساً أدق.\n" + self.stock_header())]
        lines = [f"📦 نتائج البحث ({len(hits)})" + (f" - {note}" if note else "")]
        for i, h in enumerate(hits, 1):
            sites = "، ".join(f"{s}: {stock.fmt_qty(q)}" for s, q in h.sites.items())
            lines.append(f"{i}) {h.name}{' 🏷️راكد' if h.stagnant else ''}\n   الإجمالي {stock.fmt_qty(h.total)} {h.unit} | {sites}")
        lines += ["", "اكتب رقم الصنف للتفاصيل والبدائل.", self.stock_header()]
        self.db.set_state(wa, "stock_results", {"codes": [h.code for h in hits]})
        return [M.text(wa, "\n".join(lines))]

    def stock_detail(self, u, code):
        wa = u["uid"]
        rows = self.db.stock_rows()
        hits = stock.group([r for r in rows if r["code"] == code])
        if not hits:
            return [M.text(wa, "الصنف لم يعد في المخزون.")]
        h = hits[0]
        lines = [f"📦 {h.name}", f"الكود: {h.code}", f"الإجمالي: {stock.fmt_qty(h.total)} {h.unit}"]
        lines += [f"  • {s}: {stock.fmt_qty(q)}" for s, q in h.sites.items()]
        if h.stagnant:
            lines.append("🏷️ صنف راكد: التخفيض يحدده ويعتمده مدير المبيعات (اطلب اعتماد).")
        eq = stock.equivalents(rows, h)
        if eq:
            lines.append("\n🔁 بدائل بنفس المواصفات (الراكد أولاً):")
            for e in eq:
                lines.append(f"  • {e.name}{' 🏷️' if e.stagnant else ''}: {stock.fmt_qty(e.total)}")
        lines += ["", self.stock_header()]
        return [M.text(wa, "\n".join(lines))]

    def on_document(self, u, inb):
        wa = u["uid"]
        if not self.is_mgr(u):
            return [M.text(wa, "رفع الملفات لمدير المبيعات.")]
        if not inb.filename.lower().endswith(".xlsx"):
            return [M.text(wa, "الملف يجب أن يكون Excel بصيغة xlsx.")]
        if not self.fetch_media:
            return [M.text(wa, "تعذر تنزيل الملف الآن.")]
        data = self.fetch_media(inb.media_id)
        if any(w in inb.filename.lower() for w in ("راكد", "stagnant")):
            codes = stock.import_stagnant_codes(data, self.res.item_map)
            n = self.db.mark_stagnant(codes)
            return [M.text(wa, f"تم تحديد {n} صنف كراكد من {len(codes)} كود في الملف.")]
        try:
            rows, rep = stock.import_workbook(data, self.res.options, self.res.item_map)
        except ValueError as e:
            return [M.text(wa, f"تعذر قراءة الملف: {e}")]
        if not rows:
            return [M.text(wa, "الملف لا يحتوي أصنافاً بكمية موجبة في مواقع البيع.")]
        self.db.replace_stock(rows, "ملف Excel", inb.filename, u["name"] or wa)
        lines = [f"✅ تم تحميل المخزون: {rep['items']} صنف في {rep['sites']} موقع ({rep['loaded']} سطر).",
                 f"أُهمل: {rep['zero']} سطر بكمية صفر، {rep['non_sellable']} سطر في مواقع غير قابلة للبيع."]
        if rep["uncoded"]:
            lines.append(f"⚠️ {rep['uncoded']} سطر بلا كود معياري، يبحث عنها بالاسم فقط.")
        if rep["unmapped_sites"]:
            lines.append("⚠️ مواقع لا تطابق الهيكل الجديد: " + "، ".join(rep["unmapped_sites"][:5]))
        return [M.text(wa, "\n".join(lines))]

    # ===== العميل 360 والتحصيل والمبيعات (أودو) =====
    def customer360(self, u, q):
        wa = u["uid"]
        mgr = self.is_mgr(u)
        tail = phone_tail(q)
        leads = self.db.leads_by_phone(tail) if tail else self.db.leads_by_name(q)
        leads = [l for l in leads if mgr or l["assigned_to"] == wa]
        lines = [f"👤 ملف العميل: {q}"]
        if leads:
            lines.append("طلباته في البوت:")
            lines += [f"  • #{l['id']:04d} {l['created_at'][:10]} {l['item_name']} ({views.STATUS_PLAIN.get(l['status'], '')})" for l in leads[:5]]
        else:
            lines.append("لا توجد طلبات مسجلة في البوت لك.")
        if self.odoo.enabled:
            partners = self.odoo.find_partners(q)
            if not partners:
                lines.append("\nأودو: لا يوجد عميل مطابق.")
            for p in partners[:2]:
                owner_id = p["user_id"][0] if p.get("user_id") else None
                if not mgr and (not u["odoo_user_id"] or owner_id != u["odoo_user_id"]):
                    lines.append(f"\nأودو: {p['name']} ليس من عملائك." if u["odoo_user_id"]
                                 else "\nأودو: لم يُربط حسابك بمستخدم أودو بعد. اطلب من مدير المبيعات.")
                    continue
                lines.append(f"\nأودو: {p['name']}")
                for o in self.odoo.recent_orders(p["id"], 3):
                    lines.append(f"  • {o['name']} {str(o['date_order'])[:10]} {views.money(o['amount_total'])} ({o['state']})")
                od = self.odoo.overdue_invoices(self.db.now()[:10], partner_id=p["id"])
                total = sum(i["amount_residual"] for i in od)
                lines.append(f"  المتأخر: {len(od)} فاتورة بإجمالي {views.money(total)}" if od else "  لا متأخرات. ✅")
        else:
            lines.append("\nبيانات أودو (الأوامر والمتأخرات) تظهر بعد تفعيل الربط.")
        return [M.buttons(wa, "\n".join(lines)[:1020], [("ak:credit", "🏦 اطلب ائتمان"), ("m:menu", "القائمة")])] \
            if len("\n".join(lines)) <= 1020 else [M.text(wa, "\n".join(lines)), M.buttons(wa, "ماذا بعد؟", [("ak:credit", "🏦 اطلب ائتمان"), ("m:menu", "القائمة")])]

    def _need_odoo(self, u, mgr_ok=True):
        wa = u["uid"]
        if not self.odoo.enabled:
            return [M.text(wa, "هذه الميزة تعمل بعد ربط أودو، وهي غير مفعّلة حالياً.")]
        if not self.is_mgr(u) and not u["odoo_user_id"]:
            return [M.text(wa, "لم يُربط حسابك بمستخدم أودو بعد. اطلب من مدير المبيعات ربطه من قائمة الفريق.")]
        return None

    def collect_list(self, u):
        wa = u["uid"]
        err = self._need_odoo(u)
        if err:
            return err
        inv = self.odoo.overdue_invoices(self.db.now()[:10], odoo_user_id=None if self.is_mgr(u) else u["odoo_user_id"])
        if not inv:
            return [M.text(wa, "لا توجد فواتير متأخرة. 👌")]
        by = {}
        for i in inv:
            pid, pname = i["partner_id"]
            g = by.setdefault(pid, {"name": pname, "n": 0, "sum": 0.0, "oldest": i["invoice_date_due"]})
            g["n"] += 1
            g["sum"] += i["amount_residual"]
        top = sorted(by.items(), key=lambda kv: kv[1]["oldest"])[:10]
        total = sum(g["sum"] for g in by.values())
        return [M.listing(wa, f"💰 متأخرات التحصيل: {len(by)} عميل بإجمالي {views.money(total)}\nالأقدم أولاً",
                          [(f"cl:{pid}", g["name"], f"{g['n']} فاتورة | {views.money(g['sum'])} | منذ {g['oldest']}") for pid, g in top], "العملاء")]

    def collect_action(self, u, pid, act):
        wa = u["uid"]
        err = self._need_odoo(u)
        if err:
            return err
        pid_i = int(pid)
        inv = [i for i in self.odoo.overdue_invoices(self.db.now()[:10], partner_id=pid_i,
                                                    odoo_user_id=None if self.is_mgr(u) else u["odoo_user_id"])]
        if not inv:
            return [M.text(wa, "لا فواتير متأخرة لهذا العميل (أو ليس من عملائك).")]
        name = inv[0]["partner_id"][1]
        if act == "called":
            self.db.add_activity(None, wa, "collect", f"اتصال تحصيل: {name}")
            return [M.text(wa, "تم تسجيل الاتصال. ✅")]
        if act == "promise":
            self.db.set_state(wa, "promise", {"name": name})
            return [M.text(wa, "اكتب تاريخ وعد السداد بالشكل: 2026-12-01")]
        lines = [f"💰 {name}"] + [f"  • {i['name']} استحقاق {i['invoice_date_due']}: {views.money(i['amount_residual'])}" for i in inv[:8]]
        return [M.text(wa, "\n".join(lines)),
                M.buttons(wa, "ماذا فعلت؟", [(f"cl:{pid}:called", "📞 اتصلت"), (f"cl:{pid}:promise", "🗓️ وعد بالسداد"), ("m:menu", "القائمة")])]

    def sales_lines(self):
        if not self.odoo.enabled:
            return None
        today = self.db.now_dt()
        rows = self.odoo.sales_by_user(today.strftime("%Y-%m-01"), today.strftime("%Y-%m-%d"))
        total = sum(r[2] for r in rows)
        return [f"💼 مبيعات الشهر (قبل الضريبة): {views.money(total)}"] + [f"  • {n}: {views.money(v)}" for n, _, v in rows[:6]]

    def sales_view(self, u):
        wa = u["uid"]
        err = self._need_odoo(u)
        if err:
            return err
        today = self.db.now_dt()
        rows = self.odoo.sales_by_user(today.strftime("%Y-%m-01"), today.strftime("%Y-%m-%d"))
        if self.is_mgr(u):
            return [M.text(wa, "\n".join(self.sales_lines()))]
        mine = [r for r in rows if r[1] == u["odoo_user_id"]]
        return [M.text(wa, f"📈 مبيعاتك هذا الشهر (قبل الضريبة): {views.money(mine[0][2]) if mine else views.money(0)}")]

    # ===== الفريق =====
    def team_leads(self, u):
        lines = ["📋 طلبات الفريق المفتوحة"]
        for r in self.db.users(role="rep"):
            op = self.db.leads_of(r["uid"])
            fresh = [l for l in op if l["status"] == "new"]
            lines.append(f"• {r['name']} ({r['branch'] or 'كل الفروع'}): {len(op)} مفتوح | {len(fresh)} بلا تواصل"
                         + (f" | أقدمها {views.age(fresh[-1]['created_at'], self.now())}" if fresh else ""))
        un = self.db.unassigned()
        lines.append(f"• غير مسندة: {len(un)}")
        return [M.text(u["uid"], "\n".join(lines))]

    def team_action(self, u, a, b):
        wa = u["uid"]
        if not self.is_mgr(u):
            return [M.text(wa, "إدارة الفريق لمدير المبيعات.")]
        if a == "add":
            self.db.set_state(wa, "team_add")
            return [M.text(wa, "اكتب بيانات العضو في رسالة واحدة مفصولة بفاصلة:\nمعرّف تيليجرام، الاسم، الفرع (الرياض أو الجنوب أو الكل)\n"
                               "اختياري: مدير، أودو:رقم مستخدم أودو\nمثال: 123456789، أحمد، الرياض، أودو:12\n"
                               "يعرف الشخص معرّفه بإرسال /myid للبوت.")]
        if a == "list":
            rows = self.db.users(active_only=False)[:10]
            return [M.listing(wa, "أعضاء الفريق", [(f"tm:{r['uid']}:open", r["name"] or r["uid"],
                                                    f"{'مدير' if r['role'] == 'manager' else 'مندوب'} | {r['branch'] or 'كل الفروع'} | {'نشط' if r['active'] else 'موقوف'}") for r in rows], "الأعضاء")]
        target = self.db.get_user(a)
        if not target:
            return [M.text(wa, "العضو غير موجود.")]
        if b == "open":
            btn = ("tm:%s:off" % a, "⏸️ إيقاف") if target["active"] else ("tm:%s:on" % a, "▶️ تفعيل")
            return [M.buttons(wa, f"{target['name']}\n{target['uid']}\nالدور: {target['role']} | الفرع: {target['branch'] or 'الكل'}"
                                  f"\nربط أودو: {target['odoo_user_id'] or 'غير مربوط'}", [btn])]
        if a == wa:
            return [M.text(wa, "لا يمكنك إيقاف نفسك.")]
        self.db.set_active(a, b == "on")
        return [M.text(wa, f"تم {'تفعيل' if b == 'on' else 'إيقاف'} {target['name']}.")]

    def team_add(self, u, text):
        wa = u["uid"]
        parts = [p.strip() for p in re.split(r"[،,\n]", text) if p.strip()]
        if len(parts) < 3:
            return [M.text(wa, "البيانات ناقصة. الصيغة: معرّف تيليجرام، الاسم، الفرع")]
        wid = re.sub(r"\D", "", parts[0].translate(_AR))
        if not (5 <= len(wid) <= 15):
            return [M.text(wa, "معرّف تيليجرام غير صحيح. هو أرقام فقط، ويعرفه الشخص بإرسال /myid.")]
        branch = parts[2]
        if branch in ("الكل", "كل الفروع"):
            branch = ""
        elif branch not in self.res.options["branches"]:
            return [M.text(wa, "الفرع يجب أن يكون: " + "، ".join(self.res.options["branches"]) + " أو الكل")]
        role, odoo_id = "rep", None
        for extra in parts[3:]:
            if extra == "مدير":
                role = "manager"
            m = re.match(r"(?:أودو|اودو|odoo)\s*[:：]\s*(\d+)", extra.translate(_AR), re.I)
            if m:
                odoo_id = int(m[1])
        self.db.upsert_user(wid, parts[1], role, branch, odoo_id)
        self.db.set_state(wa)
        return [M.text(wa, f"تمت إضافة {parts[1]} ({'مدير' if role == 'manager' else 'مندوب'}). ✅\nيكفي أن يرسل /start للبوت لتفتح له القائمة.")]
