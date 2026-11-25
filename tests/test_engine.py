import unittest

from marmo_sales import stock
from tests.harness import MGR, REP1, REP2, FakeOdoo, H, ids, stock_xlsx, texts


class Auth(unittest.TestCase):
    def test_unknown_user_gets_own_id_only(self):
        h = H()
        out = h.say("9999", "السلام عليكم")
        self.assertIn("معرّفك: 9999", texts(out))
        self.assertIsNone(h.db.get_user("9999"))

    def test_manager_from_env_is_created_and_inactive_blocked(self):
        h = H()
        self.assertIn("m:leads", ids(h.say(MGR, "قائمة")) + ["m:leads"])
        self.assertEqual(h.db.get_user(MGR)["role"], "manager")
        h.user(REP1, "أحمد", "rep")
        h.db.set_active(REP1, False)
        self.assertIn("غير مسجل", texts(h.say(REP1, "قائمة")))


class Menus(unittest.TestCase):
    def test_menus_fit_and_every_entry_works(self):
        h = H()
        h.team()
        h.lead(1)
        for uid, expect in ((REP1, 8), (MGR, 10)):
            out = h.say(uid, "قائمة")[0]
            self.assertEqual((out.kind, len(out.rows), out.cols), ("list", expect, 2))
            for rid in [r[0] for r in out.rows]:
                res = h.tap(uid, rid)
                self.assertTrue(res, rid)
        self.assertIn("لمدير المبيعات", texts(h.tap(REP1, "m:team")))


class Leads(unittest.TestCase):
    def setUp(self):
        self.h = H()
        self.h.team()

    def test_balanced_assignment_and_notification(self):
        h = self.h
        h.user("2003", "ماجد", "rep", "الرياض")
        l1, new, outs1 = h.app.engine.svc.ingest({"external_ref": "a", "item_name": "x", "customer_name": "ع", "phone": "0501", "branch_name": "الرياض"})
        l2, _, outs2 = h.app.engine.svc.ingest({"external_ref": "b", "item_name": "x", "customer_name": "ع", "phone": "0502", "branch_name": "الرياض"})
        owners = {h.db.lead(l1)["assigned_to"], h.db.lead(l2)["assigned_to"]}
        self.assertEqual(owners, {REP1, "2003"})            # توزيع بالتساوي
        self.assertIn(f"l:{l1}:contacted", ids(outs1))
        self.assertEqual(outs1[0].to, h.db.lead(l1)["assigned_to"])

    def test_unassigned_goes_to_manager_with_assign_button(self):
        h = H()
        h.user(MGR, "مصطفى", "manager")
        lid, new, outs = h.app.engine.svc.ingest({"external_ref": "a", "item_name": "x", "customer_name": "ع", "phone": "0501", "branch_name": "الرياض"})
        self.assertEqual(outs[0].to, MGR)
        self.assertIn(f"l:{lid}:assign", ids(outs))
        self.assertIn("لا يوجد مندوبون", texts(h.tap(MGR, f"l:{lid}:assign")))
        h.user(REP1, "أحمد", "rep", "الرياض")
        self.assertEqual(h.tap(MGR, f"l:{lid}:assign")[0].kind, "list")
        out = h.tap(MGR, f"as:{lid}:{REP1}")
        self.assertEqual(h.db.lead(lid)["assigned_to"], REP1)
        self.assertEqual(out[1].to, REP1)

    def test_dedupe_and_validation(self):
        h = self.h
        a = h.lead(1)
        b = h.lead(1)
        self.assertEqual(a, (1, True))
        self.assertEqual(b, (1, False))
        with self.assertRaises(ValueError):
            h.app.ingest({"external_ref": "z"})

    def test_status_flow_and_first_contact(self):
        h = self.h
        lid, _ = h.lead(1)
        for act, status in (("contacted", "contacted"), ("quoted", "quoted"), ("won", "won"), ("reopen", "contacted")):
            h.advance(minutes=30)
            h.tap(REP1, f"l:{lid}:{act}")
            self.assertEqual(h.db.lead(lid)["status"], status)
        l = h.db.lead(lid)
        self.assertEqual(l["first_contact_at"], "2026-11-29 09:30")

    def test_lost_requires_reason_choice(self):
        h = self.h
        lid, _ = h.lead(1)
        out = h.tap(REP1, f"l:{lid}:lost")
        self.assertIn(f"lr:{lid}:0", ids(out))
        h.tap(REP1, f"lr:{lid}:0")
        l = h.db.lead(lid)
        self.assertEqual((l["status"], l["lost_reason"]), ("lost", "السعر"))

    def test_permissions(self):
        h = self.h
        lid, _ = h.lead(1)                       # للرياض فمسند لأحمد
        self.assertIn("مسند إلى أحمد", texts(h.tap(REP2, f"l:{lid}:open")))
        self.assertIn("#0001", texts(h.tap(MGR, f"l:{lid}:open")))
        self.assertIn("مسند إلى أحمد", texts(h.say(REP2, "#1")))
        self.assertIn("غير موجود", texts(h.say(MGR, "#99")))

    def test_notes_history_and_call(self):
        h = self.h
        lid, _ = h.lead(1)
        h.tap(REP1, f"l:{lid}:note")
        h.say(REP1, "طلب عينة")
        self.assertIn("طلب عينة", texts(h.tap(REP1, f"l:{lid}:hist")))
        self.assertIn("0501112201", texts(h.tap(REP1, f"l:{lid}:call")))

    def test_paging_of_my_leads(self):
        h = self.h
        for i in range(1, 13):
            h.lead(i)
        out = h.tap(REP1, "m:leads")[0]
        self.assertEqual(len(out.rows), 10)
        self.assertEqual(out.rows[-1][0], "pg:2")
        self.assertEqual(len(h.tap(REP1, "pg:2")[0].rows), 3)


class Followups(unittest.TestCase):
    def setUp(self):
        self.h = H(BRIEF_HOUR="20")
        self.h.team()
        self.lid, _ = self.h.lead(1)

    def test_preset_reminder_and_done(self):
        h = self.h
        h.tap(REP1, f"fp:{self.lid}:2h")
        h.say(REP1, "اتصل بالعميل")
        h.app.tg.sent.clear()
        f = h.db.followups_of(REP1)[0]
        self.assertEqual(f["due_at"], "2026-11-29 11:00")
        h.advance(hours=1)
        h.app.scheduler.tick()
        self.assertEqual(h.app.tg.sent, [])                 # لم يحن الوقت بعد
        h.advance(hours=1, minutes=1)
        h.app.scheduler.tick()
        sent = [m for m in h.app.tg.sent if m["method"] == "sendMessage"]
        self.assertTrue(any("اتصل بالعميل" in m["text"] and f"f:{f['id']}:done" in m["reply_markup"] for m in sent))
        n = len(h.app.tg.sent)
        h.app.scheduler.tick()
        self.assertEqual(len(h.app.tg.sent), n)             # لا تكرار
        h.tap(REP1, f"f:{f['id']}:done")
        self.assertEqual(h.db.followups_of(REP1), [])

    def test_custom_time_validation_and_snooze(self):
        h = self.h
        h.tap(REP1, f"fp:{self.lid}:custom")
        self.assertIn("الصيغة غير صحيحة", texts(h.say(REP1, "بكرة")))
        h.say(REP1, "2026-12-01 10:30")
        h.say(REP1, "0")
        f = h.db.followups_of(REP1)[0]
        self.assertEqual(f["due_at"], "2026-12-01 10:30")
        h.tap(REP1, f"f:{f['id']}:snooze")
        self.assertEqual(h.db.followups_of(REP1)[0]["due_at"], "2026-11-30 10:00")


class Stock(unittest.TestCase):
    def setUp(self):
        self.h = H()
        self.h.team()
        self.up = self.h.doc(MGR, stock_xlsx(), "مخزون.xlsx")

    def test_upload_summary_and_no_cost_stored(self):
        t = texts(self.up)
        self.assertIn("5 صنف في 4 موقع", t)
        self.assertIn("2 سطر في مواقع غير قابلة للبيع", t)
        for row in self.h.db.stock_rows():
            self.assertNotIn("cost", " ".join(row))
            self.assertNotIn(55.5, row.values())

    def test_search_with_arabic_trade_name_and_filters(self):
        t = texts(self.h.say(REP1, "بيانكو 60×30 2 سم لامع"))
        self.assertIn("Bianco", t)
        self.assertIn("160", t)                              # 120 + 40
        self.assertIn("مصنع الرياض: 120", t)
        self.assertNotIn("Royal Gold", t)
        self.assertNotIn("999", t)                           # مواقع غير قابلة للبيع مستبعدة

    def test_family_search_order_detail_and_equivalents(self):
        h = self.h
        t = texts(h.say(REP1, "جرانيت 2 سم"))
        self.assertLess(t.index("Bianco"), t.index("Royal Gold"))
        self.assertLess(t.index("Royal Gold"), t.index("Tropical"))
        detail = texts(h.say(REP1, "1"))                     # Bianco
        self.assertIn("بدائل", detail)
        self.assertLess(detail.index("Royal Gold"), detail.index("Tropical"))   # الراكد أولاً
        self.assertIn("🏷️", detail)
        self.assertIn("راكد", texts(h.say(REP1, "2")))

    def test_filler_words_and_definite_article(self):
        h = self.h
        t = texts(h.say(REP1, "عندنا ايه بيانكو 60×30"))
        self.assertIn("Bianco", t)
        self.assertIn("Bianco", texts(h.say(REP1, "الجرانيت 3 سم")))

    def test_relaxed_search_and_empty(self):
        h = self.h
        self.assertIn("بدون شرط السمك", texts(h.say(REP1, "بيانكو 5 سم")))
        self.assertIn("لم أجد", texts(h.say(REP1, "جرانيت xyzabc")))

    def test_stale_warning_and_who_can_upload(self):
        h = self.h
        self.assertNotIn("قديمة", texts(h.say(REP1, "بيانكو")))
        h.advance(days=3)
        self.assertIn("قديمة", texts(h.say(REP1, "بيانكو")))
        self.assertIn("لمدير المبيعات", texts(h.doc(REP1, stock_xlsx(), "x.xlsx")))
        self.assertIn("xlsx", texts(h.doc(MGR, b"x", "x.csv")))

    def test_bad_file_and_stagnant_file_and_odoo_codes(self):
        h = self.h
        self.assertIn("تعذر قراءة الملف", texts(h.doc(MGR, stock_xlsx([["a", "b"]], ["كذا", "كذا"]), "bad.xlsx")))
        old_code = next(iter(h.app.res.item_map))
        new_code, ar, _ = h.app.res.item_map[old_code]
        h.doc(MGR, stock_xlsx([[old_code, "اسم قديم", "1000-FGD", 10, "م²", "", 0]]), "odoo.xlsx")
        row = h.db.stock_rows()[0]
        self.assertEqual((row["code"], row["name"]), (new_code, ar))
        h.doc(MGR, stock_xlsx([[new_code]], ["الكود"]), "قائمة راكد.xlsx")
        self.assertEqual(h.db.stock_rows()[0]["stagnant"], 1)

    def test_no_stock_loaded(self):
        h = H()
        h.team()
        self.assertIn("لم يُرفع مخزون", texts(h.say(REP1, "بيانكو")))


class Approvals(unittest.TestCase):
    def setUp(self):
        self.h = H()
        self.h.team()
        self.lid, _ = self.h.lead(1)

    def request(self, text="5% عميل مشروع"):
        self.h.tap(REP1, f"l:{self.lid}:discount")
        return self.h.say(REP1, text)

    def test_request_decide_and_notify(self):
        h = self.h
        out = self.request()
        self.assertEqual(out[-1].to, MGR)
        self.assertEqual(ids(out[-1:]), ["ap:1:yes", "ap:1:no"])
        res = h.tap(MGR, "ap:1:yes")
        self.assertEqual(h.db.approval(1)["status"], "approved")
        self.assertEqual(res[-1].to, REP1)
        self.assertIn("تم الاعتماد", res[-1].body)
        self.assertIn("سبق البت", texts(h.tap(MGR, "ap:1:no")))
        self.assertIn("اعتماد", texts(h.tap(MGR, f"l:{self.lid}:hist")))

    def test_rep_cannot_decide_and_no_self_approval(self):
        self.request()
        h = self.h
        self.assertIn("لمدير المبيعات", texts(h.tap(REP1, "ap:1:yes")))
        h.tap(MGR, f"l:{self.lid}:discount")
        h.say(MGR, "3% لنفسي")
        self.assertIn("بنفسك", texts(h.tap(MGR, "ap:2:yes")))

    def test_policy_auto_approval_when_limit_set(self):
        h = self.h
        h.app.res.policy["discount_auto_pct"] = 5
        self.assertIn("ضمن الحد المعتمد", texts(self.request("٥٪ عميل قديم")))
        self.assertEqual(h.db.approval(1)["status"], "approved")
        self.request("10% كبير")
        self.assertEqual(h.db.approval(2)["status"], "pending")

    def test_credit_request_from_menu(self):
        h = self.h
        h.tap(REP1, "ak:credit")
        out = h.say(REP1, "شركة الواحة 50000 ريال")
        self.assertEqual(h.db.approval(1)["kind"], "credit")
        self.assertEqual(out[-1].to, MGR)


class Team(unittest.TestCase):
    def setUp(self):
        self.h = H()
        self.h.team()

    def test_add_validate_deactivate(self):
        h = self.h
        self.assertIn("لمدير", texts(h.tap(REP1, "tm:add")))
        h.tap(MGR, "tm:add")
        self.assertIn("الفرع يجب", texts(h.say(MGR, "30030، خالد، مكة")))
        h.tap(MGR, "tm:add")
        h.say(MGR, "30030، خالد، الجنوب، أودو:15")
        u = h.db.get_user("30030")
        self.assertEqual((u["role"], u["branch"], u["odoo_user_id"]), ("rep", "الجنوب", 15))
        h.tap(MGR, "tm:30030:off")
        self.assertIn("غير مسجل", texts(h.say("30030", "قائمة")))
        self.assertIn("نفسك", texts(h.tap(MGR, f"tm:{MGR}:off")))

    def test_team_views(self):
        h = self.h
        h.lead(1)
        self.assertIn("أحمد", texts(h.tap(MGR, "m:teamleads")))
        self.assertEqual(h.tap(MGR, "tm:list")[0].kind, "list")
        self.assertIn("لوحة اليوم", texts(h.tap(MGR, "m:brief")))
        self.assertIn("طلباتك المفتوحة: 1", texts(h.tap(REP1, "m:brief")))


class OdooFeatures(unittest.TestCase):
    def setUp(self):
        inv = [{"name": "INV/1", "partner_id": [5, "شركة الواحة"], "invoice_date_due": "2026-10-01", "amount_residual": 5000.0, "user": 12},
               {"name": "INV/2", "partner_id": [6, "مؤسسة النخيل"], "invoice_date_due": "2026-10-15", "amount_residual": 800.0, "user": 13}]
        self.odoo = FakeOdoo(
            partners=[{"id": 5, "name": "شركة الواحة", "phone": "0501112201", "mobile": "", "user_id": [12, "أحمد"]}],
            orders=[{"name": "S0001", "date_order": "2026-11-01 10:00:00", "amount_total": 12000.0, "state": "sale"}],
            invoices=inv)
        self.h = H(**self.odoo.env())
        self.h.team()
        self.h.lead(1)

    def tearDown(self):
        self.odoo.close()

    def test_customer360_scoped_to_rep(self):
        h = self.h
        t = texts(h.say(REP1, "0501112201"))
        self.assertIn("شركة الواحة", t)
        self.assertIn("S0001", t)
        self.assertIn("المتأخر: 1 فاتورة بإجمالي 5,000", t)
        self.assertIn("ليس من عملائك", texts(h.say(REP2, "0501112201")))

    def test_collections_list_actions_and_promise(self):
        h = self.h
        out = h.tap(REP1, "m:collect")
        self.assertEqual(ids(out), ["cl:5"])                 # مندوب أودو 12 يرى عميله فقط
        self.assertEqual({r[0] for r in h.tap(MGR, "m:collect")[0].rows}, {"cl:5", "cl:6"})
        self.assertIn("INV/1", texts(h.tap(REP1, "cl:5")))
        self.assertIn("تم تسجيل الاتصال", texts(h.tap(REP1, "cl:5:called")))
        h.tap(REP1, "cl:5:promise")
        h.say(REP1, "2026-12-05")
        self.assertEqual(h.db.followups_of(REP1)[0]["due_at"], "2026-12-05 09:00")

    def test_sales_views(self):
        h = self.h
        self.assertIn("2,400", texts(h.tap(MGR, "m:sales")))
        self.assertIn("1,500", texts(h.tap(REP1, "m:sales")))
        self.assertNotIn("900", texts(h.tap(REP1, "m:sales")))

    def test_features_off_without_odoo_and_unlinked_rep(self):
        h = H()
        h.team()
        self.assertIn("غير مفعّلة", texts(h.tap(REP1, "m:collect")))
        h2 = H(**self.odoo.env())
        h2.user(REP1, "أحمد", "rep", "الرياض", None)
        self.assertIn("لم يُربط حسابك", texts(h2.tap(REP1, "m:collect")))

    def test_odoo_down_gives_friendly_message(self):
        h = self.h
        self.odoo.close()
        self.assertIn("تعذر الاتصال بأودو", texts(h.tap(REP1, "m:collect")))
        self.odoo = FakeOdoo()


class Reports(unittest.TestCase):
    def test_report_document_has_formulas(self):
        h = H()
        h.team()
        h.lead(1)
        h.tap(REP1, "l:1:contacted")
        out = h.tap(MGR, "m:report")[0]
        self.assertEqual(out.kind, "document")
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(out.data))
        self.assertEqual(wb.sheetnames, ["الملخص", "الطلبات"])
        summary = {r[0]: r[1] for r in wb["الملخص"].iter_rows(values_only=True) if r[0]}
        self.assertTrue(str(summary["إجمالي الطلبات"]).startswith("=COUNTA("))
        self.assertTrue(any("COUNTIF" in str(v) for v in summary.values()))


if __name__ == "__main__":
    unittest.main()
