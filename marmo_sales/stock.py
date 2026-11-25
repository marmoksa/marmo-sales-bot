"""المخزون: استيراد ملف Excel أو أودو، والبحث بالمواصفات، والبدائل.

لا يُخزَّن أي سعر أو تكلفة. الأعمدة غير المعروفة تُهمَل عمداً.
المواقع القابلة للبيع: FGD وSLB وSHW فقط (من 20261022-warehouse-structure).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from openpyxl import load_workbook

CODE_RE = re.compile(r"^([A-Z]{2})-([A-Z0-9]{3})-([A-Z0-9]{3})-([A-Z]{2,3})-([A-Z0-9]{3,4})-(\d{3,4})-([A-Z]{3})(?:-(\d{3}))?$")
SITE_RE = re.compile(r"(\d{4})\s*[-_/ ]\s*([A-Za-z]{3})")
_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩أإآىة", "0123456789ااايه")
_TASHKEEL = re.compile(r"[ً-ْـ]")

ALIASES = {
    "code": ["الكود الجديد", "الكود الجديد في sap", "الكود", "code", "item code", "default_code", "internal reference", "المرجع الداخلي", "كود أودو (القديم)", "كود اودو"],
    "name": ["الاسم المعياري (ar)", "الاسم", "اسم الصنف", "name", "product", "المنتج", "display_name"],
    "site": ["الموقع", "المخزن", "location", "warehouse", "site"],
    "qty": ["الرصيد", "الكمية", "quantity", "qty", "on hand", "المتاح", "available", "الكمية المتاحة"],
    "unit": ["الوحدة", "unit", "uom"],
    "stagnant": ["راكد", "stagnant"],
}
TRUE = {"نعم", "yes", "y", "1", "true", "x", "راكد", "stagnant"}

FAMILY_WORDS = {"رخام": "MR", "marble": "MR", "جرانيت": "GR", "granite": "GR", "حجر": "ST", "stone": "ST"}
SHAPE_WORDS = {"بلاط": ("TL", "TLC"), "tile": ("TL", "TLC"), "لوح": ("SL", "SLC"), "الواح": ("SL", "SLC"), "سلاب": ("SL", "SLC"),
               "slab": ("SL", "SLC"), "وزره": ("SK",), "شريحه": ("STR",), "درج": ("SR", "SRS", "SRO"), "قايمه": ("RS", "RSO")}
STOPWORDS = {norm_ for norm_ in ("عندنا ايه اي فيه في عايز عاوز اريد ابغي ابي هل يوجد موجود كم رصيد مخزون لو سمحت من علي الي مقاس سعر").split()}
FINISH_WORDS = [("غير مجلي", "UNP"), ("غير لامع", "UNP"), ("لامع", "POL"), ("مجلي", "POL"), ("جلي", "POL"), ("هوند", "HON"),
                ("ليذر", "LTH"), ("محروق", "FLM"), ("مدقوق", "BSH"), ("ساند", "SND")]


def norm(s) -> str:
    return _TASHKEEL.sub("", str(s or "")).translate(_AR).casefold().strip()


def fmt_qty(q: float) -> str:
    return f"{q:,.1f}".rstrip("0").rstrip(".")


@dataclass
class Hit:
    code: str
    name: str
    total: float = 0.0
    sites: dict = field(default_factory=dict)
    unit: str = ""
    stagnant: bool = False
    attrs: dict = field(default_factory=dict)


def parse_code(code: str) -> dict:
    m = CODE_RE.match(code or "")
    if not m:
        return {}
    return {"fam": m[1], "org": m[2], "grd": m[3], "shp": m[4], "siz": m[5], "thk": int(m[6]), "fin": m[7]}


# ---------- الاستيراد ----------

def _find_header(rows):
    for i, r in enumerate(rows[:12]):
        cells = {norm(c): j for j, c in enumerate(r) if c is not None}
        found = {}
        for key, names in ALIASES.items():
            for n in names:
                if norm(n) in cells:
                    found[key] = cells[norm(n)]
                    break
        if "qty" in found and ("code" in found or "name" in found):
            return i, found
    raise ValueError("لم أجد صف العناوين. المطلوب عمود للكمية وعمود للكود أو الاسم")


def _site(raw, options):
    raw = str(raw or "").strip()
    m = SITE_RE.search(raw)
    if m:
        fac, loc = m[1], m[2].upper()
        if fac == "4000" or loc not in options["sellable_locations"]:
            return None, False
        label = options["factories"].get(fac, fac)
        if loc == "SLB":
            label += f" ({options['locations']['SLB']})"
        return label, True
    m = re.match(r"^(\d{4})$", raw)
    if m and m[1] in options["factories"]:
        return (None, False) if m[1] == "4000" else (options["factories"][m[1]], True)
    return (raw or "غير محدد"), True   # موقع غير مطابق للهيكل الجديد: يُعرض كما هو ويُنبَّه عليه


def import_workbook(data: bytes, options: dict, item_map: dict):
    ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i, col = _find_header(rows)
    records = []
    for r in rows[hdr_i + 1:]:
        if not any(c is not None for c in r):
            continue
        get = lambda k: r[col[k]] if k in col and col[k] < len(r) else None
        records.append({k: get(k) for k in ALIASES})
    return normalize_rows(records, options, item_map)


def normalize_rows(records, options: dict, item_map: dict):
    """يحوّل سجلات خام (code, name, site, qty, unit, stagnant) إلى صفوف المخزون المعيارية."""
    out, rep = [], {"read": 0, "loaded": 0, "zero": 0, "non_sellable": 0, "unmapped_sites": set(), "uncoded": 0}
    for rec in records:
        rep["read"] += 1
        try:
            qty = float(str(rec.get("qty")).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            rep["zero"] += 1
            continue
        raw_code = str(rec.get("code") or "").strip()
        name = str(rec.get("name") or "").strip()
        if raw_code in item_map:
            code, name = item_map[raw_code][0], item_map[raw_code][1]
        else:
            code = raw_code
        attrs = parse_code(code)
        if not attrs:
            rep["uncoded"] += 1
        raw_site = str(rec.get("site") or "").strip()
        site, ok = _site(raw_site, options)
        if not ok:
            rep["non_sellable"] += 1
            continue
        if raw_site and not SITE_RE.search(raw_site) and not re.match(r"^\d{4}$", raw_site):
            rep["unmapped_sites"].add(site)
        out.append({"code": code or name or "؟", "name": name or code or "صنف بلا اسم",
                    "fam": attrs.get("fam"), "org": attrs.get("org"), "grd": attrs.get("grd"), "shp": attrs.get("shp"),
                    "siz": attrs.get("siz"), "thk": attrs.get("thk"), "fin": attrs.get("fin"),
                    "site": site or "غير محدد", "qty": qty, "unit": str(rec.get("unit") or "م²"),
                    "stagnant": 1 if norm(rec.get("stagnant")) in TRUE else 0})
    rep["loaded"] = len(out)
    rep["items"] = len({r["code"] for r in out})
    rep["sites"] = len({r["site"] for r in out})
    rep["unmapped_sites"] = sorted(rep["unmapped_sites"])
    return out, rep


def import_stagnant_codes(data: bytes, item_map: dict):
    """ملف قائمة الراكد: أي عمود فيه كود صنف (جديد أو قديم)."""
    ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
    codes = set()
    for r in ws.iter_rows(values_only=True):
        for c in r:
            s = str(c or "").strip()
            if s in item_map:
                codes.add(item_map[s][0])
            elif CODE_RE.match(s):
                codes.add(s)
    return codes


# ---------- البحث ----------

class Query:
    def __init__(self, text: str, catalog):
        t = str(text or "").translate(_AR)
        self.raw = text
        self.fam = self.org = self.thk = self.siz = self.siz_text = None
        self.shapes: tuple = ()
        self.fins: list = []
        m = re.search(r"(\d{2,3})\s*[x×*✕]\s*(\d{2,3})", t, re.I)
        if m:
            a, b = m[1], m[2]
            self.siz_text = f"{a}×{b}"
            if len(a) == 2 and len(b) == 2:
                self.siz = a + b
            t = t.replace(m[0], " ")
        else:
            m = re.search(r"\b(\d{4})\b", t)
            if m:
                self.siz = m[1]
                self.siz_text = f"{m[1][:2]}×{m[1][2:]}"
                t = t.replace(m[0], " ")
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:سم|cm)", t, re.I)
        if m:
            self.thk = round(float(m[1]) * 10)
            t = t.replace(m[0], " ")
        else:
            m = re.search(r"(\d+)\s*(?:مم|mm)", t, re.I)
            if m:
                self.thk = int(m[1])
                t = t.replace(m[0], " ")
        n = norm(t)
        for word, code in FINISH_WORDS:
            if norm(word) in n:
                self.fins.append(code)
                n = n.replace(norm(word), " ")
                break
        aliases = getattr(catalog, "aliases", {})
        names = sorted(((norm(nm), fam, tn["code"]) for fam, lst in catalog.trade_names.items() for tn in lst
                        for nm in [tn["name"]] + aliases.get(tn["name"], [])), key=lambda x: -len(x[0]))
        for nm, fam, code in names:
            if nm and re.search(rf"(?<!\w){re.escape(nm)}(?!\w)", n):
                self.org, self.fam = code, fam
                n = re.sub(rf"(?<!\w){re.escape(nm)}(?!\w)", " ", n, count=1)
                break
        fam_words = {norm(k): v for k, v in FAMILY_WORDS.items()}
        shape_words = {norm(k): v for k, v in SHAPE_WORDS.items()}
        rest = []
        for w in n.split():
            base = w[2:] if w.startswith("ال") and len(w) > 4 else w      # الجرانيت إلى جرانيت
            if base in fam_words:
                self.fam = self.fam or fam_words[base]
            elif base in shape_words:
                self.shapes = shape_words[base]
            elif w in STOPWORDS or len(w) <= 1:
                continue
            else:
                rest.append(w)
        self.tokens = rest

    def empty(self) -> bool:
        return not any((self.fam, self.org, self.thk, self.siz, self.shapes, self.fins, self.tokens))

    def match(self, row, drop=()) -> bool:
        name = norm(row["name"])
        if self.fam and row["fam"] and row["fam"] != self.fam:
            return False
        if self.org and row["org"] and row["org"] != self.org:
            return False
        if self.shapes and row["shp"] and row["shp"] not in self.shapes:
            return False
        if "thk" not in drop and self.thk and row["thk"] and row["thk"] != self.thk:
            return False
        if "siz" not in drop and self.siz and row["siz"] and row["siz"] != self.siz:
            return False
        if "fin" not in drop and self.fins and row["fin"] and row["fin"] not in self.fins:
            return False
        if not row["fam"] or not row["org"]:   # صنف بلا كود: يعتمد على النص
            if self.siz_text and "siz" not in drop and norm(self.siz_text) not in name.replace("x", "×"):
                return False
        return all(tok in name for tok in self.tokens)


def group(rows) -> list:
    hits: dict = {}
    for r in rows:
        h = hits.setdefault(r["code"], Hit(code=r["code"], name=r["name"], unit=r["unit"], stagnant=bool(r["stagnant"]),
                                           attrs={k: r[k] for k in ("fam", "org", "grd", "shp", "siz", "thk", "fin")}))
        h.sites[r["site"]] = h.sites.get(r["site"], 0) + r["qty"]
        h.total += r["qty"]
    return sorted(hits.values(), key=lambda h: (-h.total, h.name))


def search(rows, query: str, catalog, limit: int = 8):
    """يعيد (النتائج، ملاحظة). إن لم توجد نتيجة بكل الشروط يخفف السمك ثم المقاس ثم التشطيب."""
    q = Query(query, catalog)
    if q.empty():
        return [], "empty"
    for drop, note in (((), ""), (("thk",), "بدون شرط السمك"), (("thk", "siz"), "بدون شرط السمك والمقاس"),
                       (("thk", "siz", "fin"), "بدون شروط السمك والمقاس والتشطيب")):
        hits = group([r for r in rows if q.match(r, drop)])
        if hits:
            return hits[:limit], note
    return [], ""


def equivalents(rows, hit: Hit, limit: int = 5):
    """نفس الخامة والشكل والمقاس والسمك والتشطيب باسم تجاري آخر. الراكد أولاً."""
    a = hit.attrs
    if not a.get("fam"):
        return []
    same = [r for r in rows if r["fam"] == a["fam"] and r["shp"] == a["shp"] and r["siz"] == a["siz"]
            and r["thk"] == a["thk"] and r["fin"] == a["fin"] and r["org"] != a["org"]]
    hits = group(same)
    hits.sort(key=lambda h: (not h.stagnant, -h.total))
    return hits[:limit]
