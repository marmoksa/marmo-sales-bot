# بوت مبيعات مارمو

بوت تيليجرام خاص بمصطفى وفريق المبيعات. يستقبل طلبات العملاء من بوت العميل ويسندها لمندوب الفرع، ويتابعها حتى البيع، ويبحث في المخزون بالمواصفات، ويذكّر بالمتابعات، ويدير طلبات الاعتماد، ويعرض التحصيل والمبيعات من أودو (قراءة فقط) عند ربطه.

## ما الذي يفعله

| الدور | الإمكانيات |
| --- | --- |
| المندوب | طلباته، البحث في المخزون وبدائله، المتابعات، ملف العميل، طلب خصم أو ائتمان، التحصيل، مبيعاته |
| مدير المبيعات | كل ما سبق، مع لوحة اليوم، الطلبات غير المسندة، الاعتمادات، الفريق، رفع المخزون، تقرير Excel |
| النظام | إسناد تلقائي للأقل حملاً، تذكير المتابعات، ملخص الصباح، تنبيه الطلب بلا تواصل (اختياري) |

## الهيكل

```
marmo_sales_bot/
  README.md  requirements.txt  Dockerfile  fly.toml.example  .env.example
  marmo_sales/
    app.py           التجميع، حلقة تيليجرام، استقبال الطلبات
    telegram.py      محوّل تيليجرام (long polling، لا webhook)
    engine.py        محرك المحادثة والقوائم والحالات
    services.py      الإسناد والاعتمادات والمتابعات
    stock.py         استيراد المخزون والبحث والبدائل
    odoo_read.py     قراءة من أودو فقط
    scheduler.py     المهام الدورية
    briefs.py reports.py views.py db.py config.py messages.py resources.py
    simulate.py      محاكي بلا اتصال
    data/            catalog.json item_map.json aliases.json options.json policy.json
  tools/             build_item_map.py
  tests/             44 اختباراً
  docs/              الخطة، خريطة التدفق، الميزات والصلاحيات، التشغيل، الأمان، القرارات
```

## تجربة بلا اتصال

```powershell
pip install -r requirements.txt
python -m marmo_sales.simulate --demo
python -m unittest discover -s tests -t .
```

التشغيل الفعلي في `docs/التشغيل والنشر.md`. الأمان والخصوصية في `docs/الأمان والخصوصية.md`.
