# -*- coding: utf-8 -*-
"""
يبني marmo_sales/data/item_map.json (كود أودو القديم إلى الكود والاسم المعياريين)
من ملف الأصناف النظيفة المكوّدة. قراءة فقط. لا أرصدة ولا تكاليف في المخرج.

    python tools/build_item_map.py "D:/هيكله مارموا 18-8-2026/Marmo Clean Items Coded final.xlsx"
"""
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "marmo_sales" / "data"
CUSTOMER_CATALOG = ROOT.parent / "marmo_crm_bot" / "marmo_bot" / "data" / "catalog.json"


def main(src: str):
    wb = load_workbook(src, read_only=True, data_only=True)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    ix = {n: i for i, n in enumerate(rows[3])}
    items = {}
    for r in rows[4:]:
        if r[0] is None:
            continue
        old = str(r[ix["كود أودو (القديم)"]]).strip()
        items[old] = [r[ix["الكود الجديد في SAP"]], r[ix["الاسم المعياري (AR)"]], r[ix["الاسم المعياري (EN)"]]]
    out = {"meta": {"source_file": Path(src).name, "built_on": date.today().isoformat(), "items": len(items),
                    "note": "20261022-item-coding-standard"}, "items": items}
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "item_map.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("تم:", len(items), "صنف")
    if CUSTOMER_CATALOG.exists():
        shutil.copy(CUSTOMER_CATALOG, DATA / "catalog.json")
        print("نُسخ كتالوج الأسماء التجارية من مشروع بوت العميل")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
