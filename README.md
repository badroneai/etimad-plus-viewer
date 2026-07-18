# كشّاف — مرآة المنافسات والترسيات

واجهة عربية ثابتة تدمج خط الأساس التاريخي مع أحدث حقول الجلب الدوري من منصة اعتماد الرسمية. الحقول الرسمية غير الفارغة تتقدم، بينما تبقى عروض وترسيات خط الأساس محفوظة إلى أن تصبح النتيجة الرسمية مكتملة.

## المعاينة

https://badroneai.github.io/etimad-plus-viewer/

## عقد البيانات v2

- `data/awarded_index.json`: فهرس بحث/جدول صغير بلا مصفوفات العروض الثقيلة.
- `data/awarded_details/00.json` … `63.json`: تفاصيل كاملة موزعة بثبات حسب أول بايت من SHA-256 للمرجع.
- فتح بطاقة ترسية يحمّل شارد تفاصيل واحداً فقط؛ لا يوجد `data/awarded.json` أحادي ضخم ولا اعتماد على Git LFS.
- `data/manifest.json`: رقم المخطط، هوية اللقطة، أوقات المصادر، أسبقية الدمج، وSHA-256/الحجم/العدد لكل أصل.
- بطاقة التفاصيل تعرض المصدر والحداثة والمعرفات الرسمية وموعد/نمط/اكتمال ومجموعات الترسية والحقول المالية والزمنية المتاحة.
- المال يُحفظ توافقياً بالقيم الأصلية، ويُسقط أيضاً إلى هللات صحيحة عبر `Decimal(str(value))` مع فحص تطابق مجموع ترسيات الفائزين.
- فهرس المرساة والشاردات حتمية بلا وقت توليد عالمي داخلها؛ تغيير هوية التشغيل يغيّر `manifest.json` وحده ما لم يتغير سجل فعلاً.

## التصدير

من مستودع الجلب الرسمي بعد ترحيل `baseline_tenders.record_json`:

```bash
python3 scripts/export_warehouse.py \
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

يفشل وضع DB-only إذا كان خط الأساس معلناً لكن `record_json` ناقصاً، حتى لا ينشر كشّاف لقطة مبتورة بصمت.

## بوابات النشر

```bash
python3 -m unittest discover -s tests -v
node --check assets/app.js
python3 scripts/check_data_contract.py --expect-snapshot-id "run_123_1"
```

وبعد نشر GitHub Pages، يتحقق الأمر التالي من ظهور اللقطة نفسها لا من HTTP 200 فقط:

```bash
python3 scripts/check_data_contract.py \
  --base-url https://badroneai.github.io/etimad-plus-viewer \
  --expect-snapshot-id "run_123_1" \
  --wait-seconds 180
```

## تشغيل محلي

```bash
python3 -m http.server 8080
```

افتح `http://localhost:8080`.
