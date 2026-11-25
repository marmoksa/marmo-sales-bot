# -*- coding: utf-8 -*-
"""تقارير المبيعات من أودو: يومي / أمس / شهري / سنوي / تاريخ محدد.
مقسمة بالمندوب والفرع مع توتال.
"""
from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo


def _month_range(d: date):
    return date(d.year, d.month, 1).isoformat(), d.isoformat()


def _year_range(d: date):
    return date(d.year, 1, 1).isoformat(), d.isoformat()


def date_range_for(period: str, tz: ZoneInfo):
    today = date.today()
    if period == "today":
        return today.isoformat(), today.isoformat(), "اليوم"
    if period == "yesterday":
        y = today - timedelta(days=1)
        return y.isoformat(), y.isoformat(), "أمس"
    if period == "month":
        f, t = _month_range(today)
        return f, t, f"شهر {today.strftime('%m/%Y')}"
    if period == "year":
        f, t = _year_range(today)
        return f, t, f"سنة {today.year}"
    return today.isoformat(), today.isoformat(), "اليوم"


def format_report(rows_by_user, rows_by_team, date_label: str) -> str:
    def money(x): return f"{x:,.0f} ريال"

    lines = [f"📈 *تقرير المبيعات — {date_label}*\n"]

    # توتال
    total = sum(r[2] for r in rows_by_user)
    lines.append(f"💰 *الإجمالي:* {money(total)}\n")

    # بالفرع
    if rows_by_team:
        lines.append("🏢 *بالفرع:*")
        for name, _, amt in rows_by_team:
            lines.append(f"  • {name}: {money(amt)}")
        lines.append("")

    # بالمندوب
    if rows_by_user:
        lines.append("👤 *بالمندوب:*")
        for name, _, amt in rows_by_user:
            if amt > 0:
                lines.append(f"  • {name}: {money(amt)}")

    if total == 0:
        lines.append("_لا توجد مبيعات في هذه الفترة_")

    return "\n".join(lines)


def period_buttons(uid: str):
    """أزرار اختيار الفترة."""
    return [
        (f"sr:today:{uid}", "📅 اليوم"),
        (f"sr:yesterday:{uid}", "📅 أمس"),
        (f"sr:month:{uid}", "📆 الشهر"),
        (f"sr:year:{uid}", "📊 السنة"),
    ]
