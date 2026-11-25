"""نصوص البطاقات والقوائم."""
from __future__ import annotations

from datetime import datetime

from . import messages as M

STATUS = {"new": "🆕 جديد", "contacted": "📞 تم التواصل", "quoted": "📄 أُرسل عرض",
          "won": "✅ تم البيع", "lost": "❌ لم يكتمل"}
STATUS_PLAIN = {"new": "جديد", "contacted": "تم التواصل", "quoted": "أُرسل عرض", "won": "تم البيع", "lost": "لم يكتمل"}
NEXT_STEP = {"new": ("contacted", "تم التواصل"), "contacted": ("quoted", "أُرسل عرض"),
             "quoted": ("won", "تم البيع"), "won": ("reopen", "إعادة فتح"), "lost": ("reopen", "إعادة فتح")}


def parse_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M") if s else None


def age(then: str, now: datetime) -> str:
    t = parse_dt(then)
    if not t:
        return ""
    mins = int((now.replace(tzinfo=None) - t).total_seconds() // 60)
    if mins < 1:
        return "الآن"
    if mins < 60:
        return f"منذ {mins} د"
    if mins < 1440:
        return f"منذ {mins // 60} س"
    return f"منذ {mins // 1440} يوم"


def lead_text(l, owner_name: str, now: datetime) -> str:
    lines = [
        f"📌 طلب #{l['id']:04d}  |  {STATUS.get(l['status'], l['status'])}",
        f"الصنف: {l['item_name'] or ''}",
        f"الكمية: {l['quantity'] or '-'}  |  النوع: {l['customer_type'] or '-'}",
    ]
    if l["project_name"]:
        lines.append(f"المشروع: {l['project_name']}")
    lines += [f"العميل: {l['customer_name'] or ''}  {l['phone'] or ''}",
              f"الفرع: {l['branch_name'] or '-'}  |  المسؤول: {owner_name or 'غير مسند'}"]
    if l["notes"]:
        lines.append(f"ملاحظات: {l['notes']}")
    if l["status"] == "lost" and l["lost_reason"]:
        lines.append(f"سبب عدم الاكتمال: {l['lost_reason']}")
    lines.append(f"وصل: {l['created_at']} ({age(l['created_at'], now)})")
    return "\n".join(lines)


def lead_card(to, l, owner_name, now) -> M.Out:
    nxt, label = NEXT_STEP[l["status"]]
    return M.buttons(to, lead_text(l, owner_name, now),
                     [(f"l:{l['id']}:{nxt}", label), (f"l:{l['id']}:fu", "⏰ متابعة"), (f"l:{l['id']}:more", "⋯ المزيد")])


def lead_row(l):
    return (f"l:{l['id']}:open", f"#{l['id']:04d} {l['customer_name'] or ''}",
            f"{STATUS_PLAIN.get(l['status'], '')} | {l['item_name'] or ''}")


def money(x: float) -> str:
    return f"{x:,.0f} ريال"
