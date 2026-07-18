# معمارية كشّاف

## حدود النظام

كشّاف تطبيق عربي ثابت بلا backend خاص به. يستقبل إسقاطًا حتميًا من مستودع
الجلب الرسمي، يتحقق منه في CI، ثم ينشر artifact غير قابل للتبديل على GitHub
Pages. لا يجلب المتصفح من اعتماد ولا من منصة وسيطة.

```text
verified official SQLite + phase0 lock
  -> export_warehouse.py
  -> data/manifest.json + deterministic JSON assets
  -> local tests and contract
  -> viewer main
  -> Pages artifact
  -> browser bootstrap from manifest
```

## طبقات الأصول

| الطبقة | الأصول | الغرض |
| --- | --- | --- |
| التحكم | `data/manifest.json` | schema وsnapshot وقائمة الأصول وSHA-256 والعدّ |
| المنافسات | `open.json` وبقية lifecycle datasets | جداول الحالات النشطة ضمن التغطية المعلنة |
| فهرس الترسيات | `awarded_index.json` و`awarded_index_parts/*.json` | descriptor صغير وأجزاء بحث حتمية قابلة للتحميل التدريجي |
| تفاصيل الترسيات | `awarded_details/00.json` … `63.json` | البطاقة الكاملة حسب SHA-256 للمرجع |
| الكيانات والتصنيف | companies/agencies/activities/types | facets ومصادر API/SSR/sitemap مع provenance |
| التدقيق | `fetch_status.json` و`inventory.json` | حدود الاكتمال وهوية المصدر والمخزون |

في schema v3 لا يحمل `awarded_index.json` مصفوفة الترسيات كاملة. يعلن descriptor
الخوارزمية وعدد الأجزاء ومساراتها؛ يثبت manifest كل جزء كأصل مستقل. يرفض العقد
أي ref مكرر أو مفقود، أو جزء لا يطابق SHA/bytes/count، أو سجلًا بلا شظية تفاصيل.

## إقلاع الواجهة والتخزين

1. تحمل الواجهة manifest أولًا للتحقق من schema وهوية snapshot.
2. كل طلب dataset يلحق `?v=<sha256-prefix>` المأخوذ من `manifest.assets`.
3. لأن URL يتغير بتغير bytes فقط، يسمح المتصفح وCDN بالتخزين الطويل من دون
   خطر قراءة أصل قديم تحت الهوية نفسها.
4. datasets الصغيرة تحمل مباشرة. فهرس الترسيات يحمل أجزاءه تدريجيًا، ويحدث
   تقدم الواجهة مع إبقاء البحث والفلترة على الأجزاء التي اكتملت.
5. يبنى search blob المطبّع مرة واحدة لكل record عند التحميل، لا عند كل ضغطة.

## التوجيه والروابط العميقة

- `#t/<ref>` عقد ثابت لفتح بطاقة منافسة/ترسية. يحسب `computedShard(ref)` شظية
  التفاصيل مباشرة، لذلك لا يعتمد الرابط على اكتمال تحميل فهرس الترسيات.
- حالة المستكشف القابلة للمشاركة تحفظ dataset والبحث والفلاتر والصفحة داخل
  `location.hash` من دون إلغاء مسار `#t/<ref>`.
- عند إغلاق modal يعود التركيز إلى العنصر الذي فتحه، ويبقى التركيز محصورًا
  داخل modal أثناء فتحه.

## حدود المسؤولية في المتصفح

- `selectDataset` يبدل المصدر ويعيد بناء view model.
- `filterRows` دالة نقية بالنسبة إلى rows والحالة، وتعمل على search blob الجاهز.
- `openByRef` يحل المرجع من الصفوف أو شظية التفاصيل ويعرض خطأ عربيًا قابلًا
  للفهم عند الفشل.
- `loadAwardedDetail` يحمل شظية واحدة content-addressed ويتحقق من وجود المرجع.
- أخطاء الشبكة لا تتحول إلى جدول فارغ صامت؛ تظهر للمستخدم وتبقى قابلة للاختبار.

## بوابات النشر

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m mypy .
node --check assets/app.js
node --test tests/test_app.cjs
python scripts/check_data_contract.py --root .
```

ينفذ عقد مماثل على Pages الحي بهوية snapshot نفسها. نجاح build وحده لا يثبت
نشر البيانات، لكن تأخر CDN بعد نجاح push وPages يسجل كحالة تقارب معلقة ولا
يعيد تصنيف جولة الجمع السليمة كفشل مصدر.

## النمو وتاريخ Git

توزيع الأجزاء بالـhash يمنع إعادة كتابة كل الفهرس عند إضافة سجل ويضع سقفًا
صغيرًا لكل أصل. تفاصيل قرار الاحتفاظ بتاريخ البيانات، شروط تشغيله، ومنع إعادة
الكتابة التلقائية موثقة في
[`ADR-0001`](docs/adr/0001-awarded-index-partition-and-git-history.md).

## الوثائق الحاكمة

- [`CLOUD_OPERATIONS.md`](CLOUD_OPERATIONS.md): التشغيل والتحقق والتشخيص.
- [`LANGUAGE_POLICY.md`](LANGUAGE_POLICY.md): لغة المنتج والكود.
- [`CHANGELOG.md`](CHANGELOG.md): تغييرات المخطط والواجهة.
- [`LICENSE`](LICENSE): الحقوق والاستخدام.
