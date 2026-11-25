"""محاكي في سطر الأوامر: جرّب البوت بلا اتصال بتيليجرام.

    python -m marmo_sales.simulate --demo

الأوامر داخل المحاكي:
    :as 966500000002     تبديل المستخدم
    :file مسار.xlsx      إرسال ملف كمستند (للمدير)
    :quit                خروج
    رقم                  اختيار زر أو بند من آخر رسالة
    أي نص                رسالة عادية
"""
from __future__ import annotations

import argparse
import io
import os
import tempfile

from openpyxl import Workbook

from .app import App
from .config import Settings
from .messages import Inbound

MANAGER, REP_RUH, REP_SOUTH = "966500000001", "966500000002", "966500000003"


def demo_stock_bytes(app: App) -> bytes:
    """ملف مخزون تجريبي من أول أصناف خريطة الأصناف، بأرصدة وهمية للتجربة فقط."""
    wb = Workbook()
    ws = wb.active
    ws.append(["الكود الجديد", "الاسم", "الموقع", "الكمية", "الوحدة", "راكد"])
    items = list(app.res.item_map.values())
    pick = [i for i in items if "-TL-" in i[0] or "-SL-" in i[0]][:60]
    for n, (code, ar, _en) in enumerate(pick):
        ws.append([code, ar, "1000-FGD" if n % 3 else "3000-FGD", 40 + n * 7, "م²", "نعم" if n % 9 == 0 else ""])
        if n % 4 == 0:
            ws.append([code, ar, "2000-SHW", 12 + n, "م²", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def seed_demo(app: App):
    app.db.upsert_user(MANAGER, "مصطفى", "manager")
    app.db.upsert_user(REP_RUH, "أحمد", "rep", "الرياض", 12)
    app.db.upsert_user(REP_SOUTH, "سعد", "rep", "الجنوب", 13)
    rows, _ = __import__("marmo_sales.stock", fromlist=["x"]).import_workbook(
        demo_stock_bytes(app), app.res.options, app.res.item_map)
    app.db.replace_stock(rows, "تجريبي", "demo.xlsx", "demo")
    for i, (name, phone, branch, item) in enumerate([
        ("محمد العتيبي", "0501112222", "الرياض", "جرانيت Bianco بلاط 60×30 2 سم لامع"),
        ("شركة الواحة", "0553334444", "الجنوب", "جرانيت Royal Gold لوح 3 سم لامع"),
        ("خالد", "0565556666", "الرياض", "رخام Rosa بلاط 60×60 2 سم لامع"),
    ], 1):
        app.ingest({"external_ref": f"demo:{i}", "item_name": item, "customer_name": name, "phone": phone,
                    "branch_name": branch, "quantity": "20 إلى 100 م²", "customer_type": "فرد", "request_code": "DEMO"})


def show(outs):
    for o in outs:
        print(f"\n  ┌─ إلى {o.to}")
        for line in (o.body or o.filename).splitlines():
            print("  │ " + line)
        if o.kind == "buttons":
            for n, (i, t) in enumerate(o.buttons, 1):
                print(f"  │ [{n}] {t}")
        if o.kind == "list":
            for n, (i, t, d) in enumerate(o.rows, 1):
                print(f"  │ [{n}] {t}" + (f"  ({d})" if d else ""))
        print("  └─")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="بيانات تجريبية")
    ap.add_argument("--db", default=os.path.join(tempfile.gettempdir(), "marmo_sales_sim.db"))
    args = ap.parse_args()
    if os.path.exists(args.db) and args.demo:
        os.remove(args.db)
    s = Settings.from_env({"DRY_RUN": "1", "DB_PATH": args.db, "MANAGER_IDS": MANAGER})
    app = App(s)
    if args.demo:
        seed_demo(app)
    me, last = MANAGER, []
    app.engine.fetch_media = lambda path: open(path, "rb").read()
    print(__doc__)
    while True:
        try:
            line = input(f"[{me}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == ":quit":
            break
        if line.startswith(":as "):
            me = line[4:].strip()
            continue
        if line.startswith(":file "):
            path = line[6:].strip().strip('"')
            inb = Inbound(me, "document", media_id=path, filename=os.path.basename(path))
        elif line.isdigit() and last and 1 <= int(line) <= len(last):
            inb = Inbound(me, "reply", reply_id=last[int(line) - 1])
        else:
            inb = Inbound(me, "text", text=line)
        outs = app.engine.handle(inb)
        mine = [o for o in outs if o.to == me]
        last = [i for o in mine[-1:] for i, *_ in (o.buttons or o.rows)] if mine else []
        show(outs)


if __name__ == "__main__":
    main()
