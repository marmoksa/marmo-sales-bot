"""قراءة من أودو عبر xmlrpc (أودو 17). لا كتابة إطلاقاً من هذه الوحدة.

كل الدوال متزامنة. تُستدعى من المحرك، وإن فشلت يرجع المحرك رسالة مفهومة بدل الانهيار.
"""
from __future__ import annotations

import logging
import xmlrpc.client
from urllib.parse import urlparse

log = logging.getLogger(__name__)


class OdooError(Exception):
    pass


class _Mixin:
    timeout = 15

    def make_connection(self, host):
        c = super().make_connection(host)
        c.timeout = self.timeout
        return c


class _Http(_Mixin, xmlrpc.client.Transport):
    pass


class _Https(_Mixin, xmlrpc.client.SafeTransport):
    pass


class OdooReader:
    def __init__(self, settings):
        self.s = settings
        self._uid = None

    @property
    def enabled(self) -> bool:
        return self.s.odoo_enabled

    def _proxy(self, path):
        t = (_Https if urlparse(self.s.odoo_url).scheme == "https" else _Http)()
        t.timeout = self.s.odoo_timeout
        return xmlrpc.client.ServerProxy(f"{self.s.odoo_url}/xmlrpc/2/{path}", transport=t)

    def _call(self, model, method, args, kw=None):
        if not self.enabled:
            raise OdooError("ربط أودو غير مفعّل")
        try:
            if self._uid is None:
                self._uid = self._proxy("common").authenticate(self.s.odoo_db, self.s.odoo_login, self.s.odoo_api_key, {})
                if not self._uid:
                    raise OdooError("فشل تسجيل الدخول إلى أودو")
            return self._proxy("object").execute_kw(self.s.odoo_db, self._uid, self.s.odoo_api_key, model, method, args, kw or {})
        except OdooError:
            raise
        except Exception as e:  # noqa: BLE001
            raise OdooError(str(e)[:200]) from e

    # ---- العملاء ----
    def find_partners(self, text: str, limit=5):
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 8:
            tail = digits[-9:]
            domain = ["|", ["phone", "ilike", tail], ["mobile", "ilike", tail]]
        else:
            domain = [["name", "ilike", text]]
        return self._call("res.partner", "search_read", [domain],
                          {"fields": ["id", "name", "phone", "mobile", "user_id"], "limit": limit})

    def recent_orders(self, partner_id, limit=5):
        return self._call("sale.order", "search_read", [[["partner_id", "=", partner_id]]],
                          {"fields": ["name", "date_order", "amount_total", "state"], "order": "date_order desc", "limit": limit})

    def overdue_invoices(self, today: str, partner_id=None, odoo_user_id=None, limit=200):
        domain = [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
                  ["payment_state", "in", ["not_paid", "partial"]], ["invoice_date_due", "<", today]]
        if partner_id:
            domain.append(["partner_id", "=", partner_id])
        if odoo_user_id:
            domain.append(["invoice_user_id", "=", odoo_user_id])
        return self._call("account.move", "search_read", [domain],
                          {"fields": ["name", "partner_id", "invoice_date_due", "amount_residual"],
                           "order": "invoice_date_due asc", "limit": limit})

    def sales_by_user(self, date_from: str, date_to: str):
        domain = [["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"],
                  ["invoice_date", ">=", date_from], ["invoice_date", "<=", date_to]]
        rows = self._call("account.move", "read_group", [domain, ["amount_untaxed_signed:sum"], ["invoice_user_id"]],
                          {"lazy": False})
        out = []
        for r in rows:
            u = r.get("invoice_user_id")
            out.append((u[1] if u else "بلا مندوب", u[0] if u else None, float(r.get("amount_untaxed_signed") or 0)))
        return sorted(out, key=lambda x: -x[2])

    # ---- المخزون الحي (تجريبي: يعتمد على مواقع أودو الحالية) ----
    def stock_rows(self):
        quants = self._call("stock.quant", "search_read", [[["location_id.usage", "=", "internal"], ["quantity", ">", 0]]],
                            {"fields": ["product_id", "location_id", "quantity"], "limit": 0})
        pids = sorted({q["product_id"][0] for q in quants})
        codes = {}
        for i in range(0, len(pids), 200):
            for p in self._call("product.product", "read", [pids[i:i + 200], ["default_code", "display_name"]]):
                codes[p["id"]] = (p.get("default_code") or "", p.get("display_name") or "")
        return [{"code": codes[q["product_id"][0]][0], "name": codes[q["product_id"][0]][1],
                 "site": q["location_id"][1], "qty": q["quantity"]} for q in quants]

    def sales_by_team(self, date_from: str, date_to: str):
        """مبيعات مجمّعة بفريق المبيعات (الفرع)."""
        domain = [["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"],
                  ["invoice_date", ">=", date_from], ["invoice_date", "<=", date_to]]
        rows = self._call("account.move", "read_group", [domain, ["amount_untaxed_signed:sum"], ["team_id"]],
                          {"lazy": False})
        out = []
        for r in rows:
            t = r.get("team_id")
            out.append((t[1] if t else "بلا فريق", t[0] if t else None, float(r.get("amount_untaxed_signed") or 0)))
        return sorted(out, key=lambda x: -x[2])
