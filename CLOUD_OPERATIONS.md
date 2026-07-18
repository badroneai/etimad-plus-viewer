# عقد تشغيل كشّاف السحابي

هذه الوثيقة هي المرجع التشغيلي المؤسسي لكشّاف. الجلب والتخزين وبناء الإسقاط
والنشر تعمل على GitHub، ولا تعتمد على جهاز شخصي أو جلسة متصفح أو نافذة وصول
يدوية.

## حدود المستودعين

- `badroneai/etimad-official-periodic` يجلب من واجهة الزائر الرسمية، ويحفظ RAW
  قبل التحليل، ويدير قاعدة الحالة والـcheckpoints واللقطات الدائمة.
- `badroneai/etimad-plus-viewer`، وهو هذا المستودع، يحوي الإسقاط الثابت لكشّاف
  واختبارات عقد البيانات وWorkflow نشر GitHub Pages.
- بيانات المرحلة 0 بذرة تاريخية مثبتة وليست مصدر تحديث دوري. المصدر الدوري
  الوحيد هو منصة اعتماد الرسمية.

```text
Etimad visitor API
  -> official-periodic GitHub Actions
  -> verified Release snapshot
  -> Kashaf projection + local contract
  -> atomic push to viewer main
  -> Pages artifact + live contract
```

## مصادر الحقيقة

لا تنسخ أرقام التشغيل إلى وثيقة handover؛ تقرأ الحالة الحية من الأصول التالية:

1. حالة الجلب الدائمة: Release ذو الوسم `etimad-periodic-state-v1` في مستودع
   الجلب الرسمي. كل لقطة زوج مترابط من أرشيف وmanifest، ويرفع الـmanifest أخيرًا
   بصفته علامة اكتمال.
2. هوية إسقاط كشّاف ومحتواه: `data/manifest.json`. يحتوي `snapshot_id` وأوقات
   المصادر وحدود الاكتمال ودورة الحياة وSHA-256 والحجم والعدد لكل أصل منشور.
3. حالة الجلب وحدود التغطية: `data/fetch_status.json`. تبقى معلومات المرحلة 0
   فيه موسومة `current=false`، ولا تتحول العينة الجزئية إلى ادعاء اكتمال.
4. آخر نسخة عامة سليمة: <https://badroneai.github.io/etimad-plus-viewer/>.

عند اختلاف رقم مكتوب في تقرير قديم مع `manifest.json` أو `fetch_status.json`،
تتقدم الأصول الموقعة داخل اللقطة. لا يعد نجاح build وحده برهان نشر؛ يجب أن يحمل
الموقع الحي `snapshot_id` نفسه ويجتاز عقد البيانات البعيد.

## الدورة السحابية

يشغّل Workflow الجلب الرسمي جولة عند `00:17` و`06:17` و`12:17` و`18:17`
بتوقيت UTC يوميًا، ويمكن تشغيله كذلك عبر `workflow_dispatch`:

1. يستعيد أحدث لقطة صالحة بعد فحص SHA-256 وسلامة الاستخراج وSQLite وكل RAW.
2. يجلب دورة محدودة من اعتماد الرسمية ويحفظ الاستجابات RAW قبل parsing.
3. يبني لقطة تراكمية جديدة وينشر الأرشيف ثم manifest بصورة ذرية.
4. ينسخ هذا المستودع بمفتاح نشر محدود، ثم يشغّل `export_warehouse.py` على
   قاعدة الحالة الرسمية مع قفل المرحلة 0 وهوية اللقطة.
5. يشغّل الاختبارات وفحص JavaScript وعقد البيانات المحلي. عند فشل أي بوابة لا
   يدفع بيانات جديدة، وتبقى آخر نسخة سليمة منشورة.
6. يدفع تغييرات `data/` إلى `main`. يبني Workflow هذا المستودع artifact ثابتًا
   وينشره على Pages.
7. يعيد فحص الموقع الحي حتى يطابق `snapshot_id` وتنجح SHA-256 والأحجام والأعداد
   وربط descriptor فهرس الترسيات بكل أجزائه وشظايا التفاصيل.

يمنع `concurrency` كاتبين متزامنين. يستخدم الجلب `GITHUB_TOKEN` داخل مستودعه،
ويستخدم النشر السر `KASHAF_DEPLOY_KEY` المقيد بهذا المستودع فقط، مع مفتاح مضيف
GitHub مثبت في Workflow.

## التحقق والتشخيص

الفحص المحلي القياسي داخل هذا المستودع:

```bash
python3 -m unittest discover -s tests -v
python3 -m ruff check .
python3 -m mypy .
node --check assets/app.js
node --test tests/test_app.cjs
python3 scripts/check_data_contract.py --root .
```

فحص النسخة الحية بهوية اللقطة المحلية:

```bash
python3 scripts/check_data_contract.py \
  --base-url https://badroneai.github.io/etimad-plus-viewer \
  --expect-snapshot-id "$(jq -r .snapshot_id data/manifest.json)" \
  --wait-seconds 720
```

عند تعطل التحديث:

1. افحص آخر تشغيل في
   <https://github.com/badroneai/etimad-official-periodic/actions>.
2. افحص Release الحالة
   <https://github.com/badroneai/etimad-official-periodic/releases/tag/etimad-periodic-state-v1>.
3. قارن `snapshot_id` في Release و`data/manifest.json` والموقع الحي.
4. افحص تشغيل Pages في
   <https://github.com/badroneai/etimad-plus-viewer/actions>.
5. نزّل artifact التشخيصي للتشغيل الرسمي عند الحاجة؛ الـartifact دليل تشخيصي
   وليس مصدر الحالة الدائمة.

لا تعالج التعطل بإعادة الجلب من منصة وسيطة، أو بتشغيل دورة يدوية من جهاز، أو
بتجاوز بوابة المصدر. أصلح مرحلة السلسلة التي فشلت ثم أعد تشغيل Workflow الرسمي.

## التصدير اليدوي الاستثنائي

التصدير المحلي أداة تحقق أو تعافٍ فقط، وليس المسار التشغيلي المعتاد:

```bash
python3 scripts/export_warehouse.py \
  --no-plus \
  --phase0-lock /path/to/PHASE0_BASELINE.lock.json \
  --official-db /path/to/official_periodic.sqlite3 \
  --out data \
  --snapshot-id "run_example_1"
```

بعده يجب تشغيل الاختبارات وعقد البيانات قبل أي نشر. لا يُستنتج اكتمال الكون
الرسمي أو ترسيات المرحلة 0 من العدد؛ حدود الاكتمال تأتي من الأدلة المضمنة في
اللقطة فقط.

## تقارب Pages والتخزين المبني على المحتوى

يحمل المتصفح manifest أولًا، ثم يلحق بكل dataset قيمة
`?v=<sha256-prefix>` من `manifest.assets`. لذلك يمكن للمتصفح وCDN تخزين الأصل
حتى يتغير محتواه بدل فرض `cache: no-store` في كل زيارة.

العقد المحلي بوابة حمراء قبل push. بعد نجاح push وPages، قد يبقى CDN مؤقتًا
على snapshot سابق؛ يسجل الجالب الرسمي هذه الحالة `pending_convergence` وتحذيرًا
ولا يحول جولة جمع سليمة إلى فشل. يبقى العقد البعيد مطلوبًا كدليل إصدار، ويمكن
إعادته وحده من دون إعادة الجلب. إذا ظهر snapshot المتوقع ثم فشل SHA أو العد،
فهذا فشل عقد حقيقي يجب إيقاف النشر عنده.

## حدود تشغيلية معلنة

- أصل ترسيات المرحلة 0 جزئي وموسوم `hasMore=true`؛ لا يمثل الكون الرسمي الكامل.
- تغطية المنافسات النشطة والتاريخ الرسمي الكامل تبقى جزئية إلى أن تغلق بوابات
  المسح والاستكمال في خطة المشروع الرسمية.
- Release الحالة الأساسي مدعوم بـartifact لكل تشغيل لمدة 90 يومًا وأرشيف شهري
  دائم في مستودع خاص، مع restore drill فعلي.
- فحص آخر `schedule` بعد 12 ساعة والـheartbeat الشهري مدمجان؛ التشغيل اليدوي لا
  يخفي غياب الجدولة.

هذه الحدود يجب أن تبقى ظاهرة في `manifest.json` و`fetch_status.json` وفي واجهة
كشّاف؛ لا تُخفف في الوثائق أو العرض.
