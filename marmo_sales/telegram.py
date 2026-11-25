"""محوّل تيليجرام (Bot API) بالـ long polling عبر httpx. لا خادم عام ولا webhook.

الرسائل نص عادي بلا تنسيق Markdown، فلا يكسرها أي رمز في أسماء العملاء.
protect_content يمنع إعادة توجيه الرسائل وحفظها (يُفعَّل افتراضياً).
"""
from __future__ import annotations

import json
import logging

import httpx

from .messages import Inbound, Out

log = logging.getLogger(__name__)
API = "https://api.telegram.org"


def parse_update(u: dict):
    """يعيد (Inbound أو None، callback_id أو None، أمر خاص أو None)."""
    if "callback_query" in u:
        cq = u["callback_query"]
        frm = cq.get("from") or {}
        msg = cq.get("message") or {}
        if (msg.get("chat") or {}).get("type") != "private":
            return None, cq.get("id"), None
        name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
        return Inbound(uid=str(frm.get("id")), kind="reply", reply_id=cq.get("data", ""), name=name,
                       msg_id=f"upd:{u['update_id']}"), cq.get("id"), None
    m = u.get("message")
    if not m or (m.get("chat") or {}).get("type") != "private":
        return None, None, None          # المجموعات والقنوات لا تُعالَج أبداً
    frm = m.get("from") or {}
    uid = str(frm.get("id"))
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
    base = dict(uid=uid, name=name, msg_id=f"upd:{u['update_id']}")
    if "document" in m:
        d = m["document"]
        return Inbound(kind="document", media_id=d.get("file_id", ""), filename=d.get("file_name", ""),
                       mime=d.get("mime_type", ""), **base), None, None
    if "text" in m:
        t = m["text"].strip()
        if t.startswith("/"):
            cmd = t.split()[0][1:].split("@")[0].lower()
            if cmd == "myid":
                return None, None, ("myid", uid)
            if cmd in ("start", "menu", "help"):
                return Inbound(kind="text", text="قائمة", **base), None, None
            t = t[1:]
        return Inbound(kind="text", text=t, **base), None, None
    return Inbound(kind="other", **base), None, None


def render(out: Out):
    """يحوّل Out إلى (نص، reply_markup أو None)."""
    def btn(i, t):
        data = str(i)
        if len(data.encode()) > 64:
            log.error("callback_data أطول من 64 بايت: %s", data)
            data = data.encode()[:64].decode(errors="ignore")
        return {"text": t, "callback_data": data}

    if out.kind == "buttons":
        bs = [btn(i, t) for i, t in out.buttons]
        rows = [bs] if len(bs) <= 3 else [bs[i:i + 2] for i in range(0, len(bs), 2)]
        return out.body, {"inline_keyboard": rows}
    if out.kind == "list":
        if out.cols > 1:
            bs = [btn(i, t) for i, t, _ in out.rows]
            rows = [bs[i:i + out.cols] for i in range(0, len(bs), out.cols)]
        else:
            rows = [[btn(i, t + (f" | {d}" if d else ""))] for i, t, d in out.rows]
        return out.body, {"inline_keyboard": rows}
    return out.body, None


class TelegramClient:
    def __init__(self, settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=40)
        self.sent: list = []                 # في وضع التجربة تُحفظ الرسائل هنا بدل إرسالها

    def redact(self, text) -> str:
        return str(text).replace(self.s.tg_token, "***") if self.s.tg_token else str(text)

    def call(self, method: str, data: dict | None = None, files: dict | None = None, timeout: int | None = None):
        if self.s.dry_run:
            self.sent.append({"method": method, **(data or {})})
            return {"file_path": "x"} if method == "getFile" else {}
        try:
            r = self.http.post(f"{API}/bot{self.s.tg_token}/{method}", data=data, files=files,
                               timeout=timeout or 40)
            j = r.json()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(self.redact(e)) from None
        if not j.get("ok"):
            raise RuntimeError(f"تيليجرام رفض {method}: {str(j.get('description'))[:200]}")
        return j["result"]

    def get_updates(self, offset: int, timeout: int = 25):
        if self.s.dry_run:
            return []
        return self.call("getUpdates", {"offset": offset, "timeout": timeout,
                                        "allowed_updates": json.dumps(["message", "callback_query"])},
                         timeout=timeout + 15)

    def answer_callback(self, cb_id):
        try:
            self.call("answerCallbackQuery", {"callback_query_id": cb_id})
        except Exception:  # noqa: BLE001  إغلاق دائرة التحميل ليس حرجاً
            log.debug("تعذر الرد على callback")

    def send(self, out: Out):
        common = {"chat_id": out.to}
        if self.s.protect_content:
            common["protect_content"] = "true"
        if out.kind == "document":
            self.call("sendDocument", {**common, "caption": out.body},
                      files={"document": (out.filename, out.data)})
            return
        text, markup = render(out)
        payload = {**common, "text": text or "-"}
        if markup:
            payload["reply_markup"] = json.dumps(markup, ensure_ascii=False)
        self.call("sendMessage", payload)

    def download(self, file_id: str) -> bytes:
        meta = self.call("getFile", {"file_id": file_id})
        if self.s.dry_run:
            return b""
        r = self.http.get(f"{API}/file/bot{self.s.tg_token}/{meta['file_path']}")
        r.raise_for_status()
        return r.content


class Deliverer:
    """يرسل الرسائل. فشل رسالة واحدة لا يوقف الباقي."""

    def __init__(self, client: TelegramClient):
        self.client = client

    def deliver(self, outs):
        for o in outs:
            try:
                self.client.send(o)
            except Exception as e:  # noqa: BLE001
                log.warning("فشل إرسال رسالة إلى %s: %s", o.to, self.client.redact(e))
