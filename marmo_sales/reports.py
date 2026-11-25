"""تقرير Excel لمدير المبيعات: ورقة الملخص بمعادلات حية فوق ورقة الطلبات."""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .views import STATUS_PLAIN, parse_dt

COLUMNS = [
    ("رقم الطلب", "id", 10), ("وصل في", "created_at", 18), ("الصنف", "item_name", 38),
    ("كود الطلب", "request_code", 28), ("الكمية", "quantity", 16), ("نوع الطلب", "customer_type", 14),
    ("المشروع", "project_name", 20), ("الفرع", "branch_name", 10), ("العميل", "customer_name", 18),
    ("الجوال", "phone", 14), ("المسؤول", "owner", 16), ("الحالة", "status_ar", 12),
    ("أول تواصل", "first_contact_at", 18), ("دقائق حتى أول تواصل", "mins_to_contact", 16),
    ("سبب عدم الاكتمال", "lost_reason", 16), ("ملاحظات", "notes", 26),
]
COL = {k: get_column_letter(i + 1) for i, (_, k, _) in enumerate(COLUMNS)}
FILL, FONT = PatternFill("solid", fgColor="1F3A5F"), Font(bold=True, color="FFFFFF")


def build_xlsx(leads, period, owner_name, branches, reps, tz) -> io.BytesIO:
    wb = Workbook()
    ws_s, ws = wb.active, wb.create_sheet("الطلبات")
    ws_s.title = "الملخص"
    for sh in (ws_s, ws):
        sh.sheet_view.rightToLeft = True
    ws.append([h for h, _, _ in COLUMNS])
    for c in ws[1]:
        c.font, c.fill, c.alignment = FONT, FILL, Alignment(horizontal="center")
    for l in leads:
        d = dict(l)
        d["owner"] = owner_name(l["assigned_to"]) or "غير مسند"
        d["status_ar"] = STATUS_PLAIN.get(l["status"], l["status"])
        a, b = parse_dt(l["created_at"]), parse_dt(l["first_contact_at"])
        d["mins_to_contact"] = int((b - a).total_seconds() // 60) if a and b else ""
        ws.append([d.get(k) if d.get(k) is not None else "" for _, k, _ in COLUMNS])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, str):
                c.data_type = "s"
    for i, (_, _, w) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    last = max(len(leads) + 1, 2)
    rng = lambda k: f"'الطلبات'!${COL[k]}$2:${COL[k]}${last}"

    ws_s["A1"] = "تقرير مبيعات مارمو"
    ws_s["A1"].font = Font(bold=True, size=14)
    ws_s["A2"], ws_s["B2"] = "المصدر", "قاعدة بيانات بوت المبيعات"
    ws_s["A3"], ws_s["B3"] = "الفترة", period
    ws_s["A4"], ws_s["B4"] = "وقت الاستخراج", datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    ws_s["A6"], ws_s["B6"] = "إجمالي الطلبات", f"=COUNTA({rng('id')})"
    state = {"r": 8}

    def block(title, key, items):
        r = state["r"]
        for col, t in ((1, title), (2, "العدد")):
            c = ws_s.cell(r, col, t)
            c.font, c.fill = FONT, FILL
        for i, it in enumerate(items, 1):
            ws_s.cell(r + i, 1, it)
            ws_s.cell(r + i, 2, f"=COUNTIF({rng(key)},A{r + i})")
        state["r"] = r + len(items) + 2
        return r + 1

    block("حسب الفرع", "branch_name", branches)
    block("حسب المسؤول", "owner", reps + ["غير مسند"])
    first = block("حسب الحالة", "status_ar", list(STATUS_PLAIN.values()))
    won, lost = first + 3, first + 4
    r = state["r"]
    ws_s.cell(r, 1, "نسبة البيع من الطلبات المغلقة")
    ws_s.cell(r, 2, f'=IF(B{won}+B{lost}=0,"غير متاح بعد",B{won}/(B{won}+B{lost}))').number_format = "0%"
    ws_s.cell(r + 1, 1, "متوسط الدقائق حتى أول تواصل")
    ws_s.cell(r + 1, 2, f'=IFERROR(AVERAGE({rng("mins_to_contact")}),"غير متاح بعد")')
    state["r"] = r + 3
    reasons = sorted({l["lost_reason"] for l in leads if l["lost_reason"]})
    if reasons:
        block("أسباب عدم الاكتمال", "lost_reason", reasons)
    ws_s.column_dimensions["A"].width = 34
    ws_s.column_dimensions["B"].width = 34
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
