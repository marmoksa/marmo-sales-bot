"""ملخص الصباح ولوحة اليوم. كل الدوال تعيد نصاً."""
from __future__ import annotations

from . import views


def rep_brief(db, user, now) -> str:
    wa = user["uid"]
    open_leads = db.leads_of(wa)
    fresh = [l for l in open_leads if l["status"] == "new"]
    fus = db.followups_of(wa, upto=db.now()[:10] + " 23:59")
    lines = [f"☀️ صباح الخير {user['name'] or ''}", f"طلباتك المفتوحة: {len(open_leads)}",
             f"بانتظار أول تواصل: {len(fresh)}"]
    for l in fresh[:3]:
        lines.append(f"  • #{l['id']:04d} {l['customer_name']} ({views.age(l['created_at'], now)})")
    lines.append(f"متابعات اليوم والمتأخرة: {len(fus)}")
    for f in fus[:3]:
        lines.append(f"  • {f['due_at'][5:]} {f['text'] or 'متابعة'}")
    return "\n".join(lines)


def rep_summary_line(db, user) -> str:
    """سطر واحد يلخص يوم المندوب."""
    wa = user["uid"]
    fresh = len([l for l in db.leads_of(wa) if l["status"] == "new"])
    fus = len(db.followups_of(wa, upto=db.now()[:10] + " 23:59"))
    return f"جديد بلا تواصل: {fresh} | متابعات اليوم: {fus}"


def manager_dashboard(db, now, sales_lines=None) -> str:
    day = db.now()[:10]
    counts = db.day_counts(day)
    new_all = db.uncontacted()
    unassigned = db.unassigned()
    lines = [f"📊 لوحة اليوم {day}",
             f"طلبات وصلت اليوم: {counts['new']}  |  تم بيعها اليوم: {counts['won']}",
             f"بانتظار أول تواصل: {len(new_all)}"
             + (f" (أقدمها {views.age(new_all[0]['created_at'], now)})" if new_all else ""),
             f"غير مسندة: {len(unassigned)}",
             f"متابعات مستحقة اليوم: {db.followup_count()}",
             f"اعتمادات معلقة: {len(db.pending_approvals())}", ""]
    lines.append("لكل مندوب (مفتوح | بلا تواصل):")
    for u in db.users(role="rep"):
        op = db.leads_of(u["uid"])
        lines.append(f"  • {u['name']}: {len(op)} | {len([l for l in op if l['status'] == 'new'])}")
    if sales_lines:
        lines += [""] + sales_lines
    return "\n".join(lines)


def manager_summary_line(db) -> str:
    return (f"بلا تواصل: {len(db.uncontacted())} | غير مسندة: {len(db.unassigned())} | "
            f"اعتمادات: {len(db.pending_approvals())} | متابعات: {db.followup_count()}")
