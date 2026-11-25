"""منطق الأعمال: استقبال الطلبات، الإسناد، الاعتمادات، المتابعات. يعيد رسائل ولا يرسلها."""
from __future__ import annotations

import re
from datetime import timedelta

from . import messages as M
from . import views

_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٪", "0123456789%")
REQUIRED = ("external_ref", "item_name", "customer_name", "phone", "branch_name")


def phone_tail(text: str) -> str:
    d = "".join(ch for ch in str(text).translate(_AR) if ch.isdigit())
    return d[-9:] if len(d) >= 9 else ""


class Services:
    def __init__(self, settings, db, res):
        self.s, self.db, self.res = settings, db, res

    # ---- الأشخاص ----
    def managers(self):
        return self.db.users(role="manager")

    def name_of(self, uid) -> str:
        u = self.db.get_user(uid) if uid else None
        return (u["name"] or uid) if u else ""

    def pick_rep(self, branch: str):
        """الأقل حملاً بين مندوبي الفرع النشطين. يعيد None عند عدم وجود أحد."""
        reps = [u for u in self.db.users(role="rep") if (not u["branch"] or u["branch"] == branch)]
        if not reps:
            return None
        load = lambda u: len(self.db.leads_of(u["uid"]))
        return min(reps, key=lambda u: (load(u), u["name"] or ""))["uid"]

    # ---- استقبال الطلب ----
    def ingest(self, payload: dict):
        """يعيد (lead_id, is_new, outs). لا يرسل شيئاً بنفسه."""
        missing = [k for k in REQUIRED if not str(payload.get(k) or "").strip()]
        if missing:
            raise ValueError("حقول ناقصة: " + ", ".join(missing))
        lead_id, is_new = self.db.add_lead(payload)
        if not is_new:
            return lead_id, False, []
        outs = []
        rep = self.pick_rep(payload["branch_name"]) if self.s.assign_mode == "balanced" else None
        if rep:
            self.db.assign(lead_id, rep)
            self.db.add_activity(lead_id, "system", "assign", f"إسناد تلقائي إلى {self.name_of(rep)}")
            outs.append(self.new_lead_notice(rep, lead_id))
        else:
            for m in self.managers():
                outs.append(self.new_lead_notice(m["uid"], lead_id, unassigned=True))
        return lead_id, True, outs

    def new_lead_notice(self, to, lead_id, unassigned=False) -> M.Out:
        l = self.db.lead(lead_id)
        now = self.db.now_dt()
        o = views.lead_card(to, l, self.name_of(l["assigned_to"]), now)
        if unassigned and l["assigned_to"] is None:
            o = M.buttons(to, views.lead_text(l, "غير مسند", now) + "\n\nلم يُسند تلقائياً.",
                          [(f"l:{lead_id}:assign", "👤 إسناد"), (f"l:{lead_id}:open", "فتح")])
        return o

    def assign(self, lead_id, uid, by) -> list:
        self.db.assign(lead_id, uid)
        self.db.add_activity(lead_id, by, "assign", f"إسناد إلى {self.name_of(uid)}")
        return [self.new_lead_notice(uid, lead_id)]

    # ---- المتابعات ----
    def due_from_preset(self, preset: str):
        now = self.db.now_dt()
        if preset == "2h":
            d = now + timedelta(hours=2)
        elif preset == "today17":
            d = now.replace(hour=17, minute=0)
            if d <= now:
                d += timedelta(days=1)
        elif preset == "tom10":
            d = (now + timedelta(days=1)).replace(hour=10, minute=0)
        elif preset == "3d":
            d = (now + timedelta(days=3)).replace(hour=10, minute=0)
        else:
            return None
        return d.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def parse_when(text: str):
        t = str(text).translate(_AR).strip()
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}):(\d{2}))?", t)
        if not m:
            return None
        hh, mm = (int(m[2]), int(m[3])) if m[2] else (9, 0)
        if hh > 23 or mm > 59:
            return None
        return f"{m[1]} {hh:02d}:{mm:02d}"

    # ---- الاعتمادات ----
    def request_approval(self, kind: str, user, text: str, lead_id=None):
        """يعيد (approval_id, outs_to_requester, outs_to_managers)."""
        label = "خصم" if kind == "discount" else "ائتمان"
        limit = self.res.policy.get("discount_auto_pct" if kind == "discount" else "credit_auto_amount")
        auto = False
        if limit is not None:
            nums = re.findall(r"\d+(?:\.\d+)?", text.translate(_AR))
            if kind == "discount":
                m = re.search(r"(\d+(?:\.\d+)?)\s*%", text.translate(_AR))
                auto = bool(m) and float(m[1]) <= float(limit)
            else:
                auto = bool(nums) and float(nums[0]) <= float(limit)
        if auto:
            aid = self.db.add_approval(kind, user["uid"], text, lead_id, status="approved", decided_by="auto", auto=1)
            return aid, [M.text(user["uid"], f"✅ اعتماد {label} رقم {aid}: ضمن الحد المعتمد تلقائياً.")], []
        aid = self.db.add_approval(kind, user["uid"], text, lead_id)
        notice = []
        for m in self.managers():
            if m["uid"] == user["uid"]:
                continue
            o = M.buttons(m["uid"], f"🔔 طلب اعتماد {label} رقم {aid}\nمن: {user['name']}"
                          + (f"\nالطلب: #{lead_id:04d}" if lead_id else "") + f"\n{text}",
                          [(f"ap:{aid}:yes", "✅ اعتماد"), (f"ap:{aid}:no", "❌ رفض")])
            notice.append(o)
        return aid, [M.text(user["uid"], f"تم إرسال طلب اعتماد الـ{label} رقم {aid} لمدير المبيعات.")], notice

    def decide(self, aid: int, yes: bool, manager):
        a = self.db.approval(aid)
        if not a:
            return [M.text(manager["uid"], "طلب الاعتماد غير موجود.")]
        if a["status"] != "pending":
            return [M.text(manager["uid"], f"سبق البت في هذا الطلب ({'اعتماد' if a['status'] == 'approved' else 'رفض'}).")]
        if a["requested_by"] == manager["uid"]:
            return [M.text(manager["uid"], "لا يمكنك اعتماد طلبك بنفسك. يعتمده مدير آخر.")]
        self.db.decide(aid, "approved" if yes else "rejected", manager["uid"])
        if a["lead_id"]:
            self.db.add_activity(a["lead_id"], manager["uid"], "approval", f"{'اعتماد' if yes else 'رفض'}: {a['text']}")
        verdict = "تم الاعتماد" if yes else "تم الرفض"
        notice = M.text(a["requested_by"], f"قرار طلب الاعتماد رقم {aid}: {verdict} من {manager['name']}.\n{a['text']}")
        return [M.text(manager["uid"], f"{verdict} للطلب رقم {aid}."), notice]
