# كشّاف — مرآة المنافسات والترسيات

واجهة عربية ثابتة تدمج خط الأساس التاريخي مع أحدث حقول الجلب الدوري من منصة اعتماد الرسمية. الحقول الرسمية غير الفارغة تتقدم، بينما تبقى عروض وترسيات خط الأساس محفوظة إلى أن تصبح النتيجة الرسمية مكتملة.

## المعاينة

https://badroneai.github.io/etimad-plus-viewer/

## عقد البيانات v2

- `data/awarded_index.json`: فهرس بحث/جدول صغير بلا مصفوفات العروض الثقيلة.
- `data/awarded_details/00.json` … `63.json`: تفاصيل كاملة موزعة بثبات حسب أول بايت من SHA-256 للمرجع.
- فتح بطاقة ترسية يحمّل شارد تفاصيل واحداً فقط؛ لا يوجد `data/awarded.json` أحادي ضخم ولا اعتماد على Git LFS.
- `data/manifest.json`: رقم المخطط، هوية اللقطة، أوقات المصادر، أسبقية الدمج، وSHA-256/الحجم/العدد لكل أصل.
- دورة الحياة لا تُستنتج من عداد `remainingDays` القديم: تُعاد من الموعد والحالة الرسمية عند وقت اللقطة، وتُعرض كـ`open`/`awarding`/`examination`/`cancelled`/`unknown`. تغطية المنافسات النشطة موسومة جزئية صراحة حتى يكتمل المسح الرسمي النشط.
- سجل قاعدة البيانات يحفظ `componentDetails` و`_freshness` و`_evidence` ومسار RAW ونسخة المحلل في بطاقة كشّاف، من دون مضاعفة المصدر عند دمجه مع خط الأساس.
- بطاقة التفاصيل تعرض المصدر والحداثة والمعرفات الرسمية وموعد/نمط/اكتمال ومجموعات الترسية والحقول المالية والزمنية المتاحة.
- المال يُحفظ توافقياً بالقيم الأصلية، ويُسقط أيضاً إلى هللات صحيحة عبر `Decimal(str(value))` مع فحص تطابق مجموع ترسيات الفائزين.
- فهرس المرساة والشاردات حتمية بلا وقت توليد عالمي داخلها؛ تغيير هوية التشغيل يغيّر `manifest.json` وحده ما لم يتغير سجل فعلاً.

## التصدير

من مستودع الجلب الرسمي بعد ترحيل `baseline_tenders.record_json`:

```bash
python3 scripts/export_warehouse.py \
  --no-plus \
  --phase0-lock /path/to/PHASE0_BASELINE.lock.json \
  --official-db /path/to/official_periodic.sqlite3 \
  --out data \
  --snapshot-id "run_123_1"
```

للتهيئة المحلية من Phase 0 مع overlay رسمي اختياري:

```bash
python3 scripts/export_warehouse.py \
  --plus-warehouse /path/to/plus_warehouse \
  --official-db /path/to/official_periodic.sqlite3
```

يفشل وضع DB-only إذا غاب lock الموثوق، أو تعارض مع `baseline_awarded` في DB meta، أو كان `record_json` ناقصاً. لا يُستنتج اكتمال awarded من العدد مطلقاً.

## بوابات النشر

```bash
python3 -m unittest discover -s tests -v
node --check assets/app.js
python3 scripts/check_data_contract.py --expect-snapshot-id "run_123_1"
```

وبعد نشر GitHub Pages، يتحقق الأمر التالي من اللقطة وكل أصل مذكور فيها: JSON والحجم وSHA-256 والعدد وربط فهرس awarded بكل الشاردات. عند تأخر CDN يعيد الأصول الفاشلة فقط لتفادي إعادة تنزيل اللقطة كاملة:

```bash
python3 scripts/check_data_contract.py \
  --base-url https://badroneai.github.io/etimad-plus-viewer \
  --expect-snapshot-id "run_123_1" \
  --wait-seconds 720
```

النشر يتم عبر `.github/workflows/pages.yml` من artifact ثابت لا عبر legacy branch build، ولا يبدأ قبل نجاح الاختبارات وفحص JavaScript وعقد البيانات المحلي.

## تشغيل محلي

```bash
python3 -m http.server 8080
```

افتح `http://localhost:8080`.
