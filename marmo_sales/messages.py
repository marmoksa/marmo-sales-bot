"""رسائل الدخول والخروج المستقلة عن قناة المراسلة. المحرك يتعامل معها فقط."""
from __future__ import annotations

from dataclasses import dataclass, field


def fit(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class Inbound:
    uid: str                   # معرّف المستخدم في تيليجرام
    kind: str                  # text | reply | document | other
    text: str = ""
    reply_id: str = ""
    media_id: str = ""
    filename: str = ""
    mime: str = ""
    msg_id: str = ""
    name: str = ""


@dataclass
class Out:
    to: str
    kind: str = "text"         # text | buttons | list | document
    body: str = ""
    buttons: list = field(default_factory=list)   # [(id, title)]
    rows: list = field(default_factory=list)      # [(id, title, description)]
    list_button: str = ""
    cols: int = 1              # عدد أعمدة الأزرار في القوائم (القائمة الرئيسية 2)
    data: bytes | None = None
    filename: str = ""


def text(to, body, **kw) -> Out:
    return Out(to=to, kind="text", body=str(body)[:4000], **kw)


def buttons(to, body, btns, **kw) -> Out:
    return Out(to=to, kind="buttons", body=str(body)[:4000], buttons=[(str(i), fit(t, 32)) for i, t in btns][:4], **kw)


def listing(to, body, rows, button="", cols=1, **kw) -> Out:
    rows = [(str(i), fit(t, 40), fit(d, 60)) for i, t, d in rows][:10]
    return Out(to=to, kind="list", body=str(body)[:4000], rows=rows, list_button=button, cols=cols, **kw)


def document(to, data: bytes, filename: str, caption: str = "") -> Out:
    return Out(to=to, kind="document", body=fit(caption, 1000), data=data, filename=filename)
