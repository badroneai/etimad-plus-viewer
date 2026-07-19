# وثيقة تصميم إعادة ضبط عقد تغطية الأيام (Phase 2.1) — مراجعة أولية قبل أي كود جديد

## 1) الثابت الجوهري (معتمد كمبدأ واحد)

**الجهة:** سجل `intervals` النهائي في `schema-5` (بدون أي اعتماد على المسار التاريخي أو توليد الجيل).

الصياغة الآلية المقترحة:

> لكل يوم `d` في النطاق `[1900-01-01, 2101-01-01)`:
>
> `state(d)` يجب أن يكون بالضبط أحد ثلاثة أحوال:
> 1) `covered`  
> 2) `terminal_gap` وله سبب غياب موثّق  
> 3) `pending`
>  
> بشرط أن يكون `state(d)` ناتج تقسيم نطاق ثابت غير متداخل وغير متعرّج من فترات `intervals`.
>
> ويجب أن تتحقق العلاقات:
>
> - مجموع `units` في `intervals` + `units_pending` = `units_total` دومًا.
> - إذا `units_pending = 0` فـ `cycle_terminal = True` ويُسمح بـ `complete=True` فقط مع شروط سلامة التكرار الخام وغياب تعارض الهوية.
> - في أي لحظة، إذا وُجدت `interval` بحالة غير `covered` أو `terminal_gap` أو تداخل/خروج عن المجال، يفشل العقد مباشرة.
>
> بمعنى بسيط: **النتيجة النهائية للـ 73k يوم هي الحقيقة الحقيقية للعقد، وليس تاريخ الوصول إلى النتيجة.**

## 2) فحصات عقد التغطية الحالية — تصنيف

التصنيف التالي يخص فحوص `schema-5` في ملف العقد داخل:
- `assert_active_interval_coverage_contract(...)`  
- `_assert_single_day_refinement_contract(...)`

**سؤال 2 - الشمولية:** لا يوجد اختبار واحد يفحص الثابت الجوهري على snapshot الإنتاج بالكامل حالياً.  
جميع الاختبارات الحالية تعمل على حالات fixture وتغطية حالات متفرقة (`fail-closed`, partial, complete, gap, authority) لا تغطي "snapshot كامل" كتحقق واحد متسق على كل الأيام.

### أ) فحوص تخدم الثابت الجوهري (تبقى)
- صحة المجال:
  - وجود `coverage_domain` ثابت (`from_day`, `to_day_exclusive`, timezone، وحدات).
  - `intervals` ضمن المجال، غير متداخلة، مرتبة، ولكل فاصل `from < to`.
  - حالة كل فاصل واحدة من `{covered, terminal_gap}`.
- هندسة النطاق:
  - `covered/unit` و `gap/unit` و `pending` ومتغيرات الأوراق مشتقة من `intervals`.
  - `coverage_percent`, `traversal_percent`, `geometry_error_count=0`.
  - `geometry_complete` في صيغة نهاية المجال (حين يغطي `intervals` كامل المجال).
- اتساق الحوكمة النهائية:
  - `cycle_terminal = (pending == 0)`.
  - `complete = terminal ∧ no_pending_gap ∧ raw_replay_valid ∧ identity_conflict_count==0`.
  - طابق `phase` مع `complete/terminal`.
- سلامة عدديّة عامة:
  - صحة أنواع/أزمنة/هويات/أحجام/أعلام `booleans`.

### ب) فحوص حادثية (مرتبطة بطريقة التدرّج وليس بالنتيجة النهائية)
- `generation_history` في `temporal_reconciliation` (تسلسل الأجيال، قيود الجيل-3، التحقق المتعلق بالانجراف التاريخي بين الجيلين).
- تطابق `state_generation` و `baseline` مع `generation_history`.
- شروط `closing_proofs` و `generation_union_*` و `bijection_*` في مسارات `sealed/collecting/blocked`.
- خريطة أسباب الـ `blocked` وارتباطها بمحددات terminal gap.

### ملاحظة اختبارات `tests/test_interval_coverage_contract.py`
- تقسيم الاختبارات نفسه يوضح الازدواج:
  - `test_interval_geometry_and_arithmetic_fail_closed`: تخدم الثابت الجوهري مباشرة.
  - `test_partial_and_terminal_gap_progress_are_honest`: تخدم الثابت (فكرة terminal/partial).
  - `test_terminal_leaf_shape_fails_closed_but_accepts_extra_fields`: تخدم الثابت (سلامة الأوراق) مع فحوص شكلية.
  - `test_schema5_cannot_smuggle_authority...` و `test_historical_schema4_authority_is_summary_only`: فحوص أمان إضافية غير جوهرية.
  - `test_complete_progressive_coverage_is_not_snapshot_authority` و `test_outer_scan...` و `test_partial_schema5_accepts...`: فحوص اتساق تشغيلية.
  - `test_refinement_shape_and_replay_metrics_fail_closed` و `test_temporal_reconciliation_requires_converged_generation_and_proofs`: فحوص مسار/أثر جانبي (incident-path) وليست جوهراً للحالة النهائية.

### ج) فحوص تكرارية/تطبيقيّة على نفس المبدأ (مرجحة للحذف أو دمجها)
- فحوص متعددة في `test_interval_coverage_contract.py` و `test_cardinality...` تكرر إدخال نفس العيب في حالة واحدة (مثلاً تغيير حقل واحد في JSON لاختبار رسالة خطأ).
- هذه فئات مهمة لاختبار المقاومة، لكن ليست كلّها تحقق الثابت الجوهري.
- هذه المجموعة لا يجب أن تُستخدم كمحرك القبول النهائي للعقد؛ يجب أن تبقى كتحقّق اختباري فقط.

## 3) اقتراح إعادة كتابة العقد (بديل مباشر قبل المسار)

### المبادئ
1. فصل **حالة النتيجة النهائية** عن **مسار الوصول التاريخي**.
2. أي حالة نهائية صحيحة يجب أن تمر بغض النظر عن وجود `drift` بين الجيل 2 و3 إذا كانت القيم الحالية متوافقة مع نتيجة الجدول اليومي.
3. أي فشل في بيانات المسار التاريخي لا يجب أن يقلب `interval coverage` الموثقة إذا كانت النتيجة النهائية صحيحة وصالحة.

### المقترح العملي
- إدخال دالة محورية جديدة:
  - `assert_interval_day_ledger_invariant(progress)`  
  تتحقق فقط من partitioning الصحيح للنطاق، وتشتق الحالة النهائية لكل يوم، وتتحقق من معادلات coverage/phase/complete/terminal.
- إبقاء دالة `assert_active_interval_coverage_contract` كمثبتة “للهوية الفنية” ثم:
  - 1) تُستدعى دالة invariant أولًا.
  - 2) تُستدعى فحوص المسار التاريخي كـ `assert_temporal_path_audit` فقط، لكن لا تُعتبر شرط قبول التغطية النهائية.
- شرط قبول `schema-5` يصبح:
  - عقد التغطية المقبولة إذا وفقط إذا مرّت `invariant` + صحة الأدلة الخام العامة (`raw_replay_valid` إلزامي حسب الحالة)،
  - أما مشاكل الانحراف داخل الجيل-3 فلا تعطل النتيجة النهائية إلا إذا أثّرت مباشرة على أدلة الأيام أو أصابت الـ `raw paths`/الهاشات.

### أثر مباشر على المشكلة الحالية
- حالة `active_scan` الصحيحة بنكهة `day_close_refresh` (سقف نظيف) لا تفشل بسبب شرط “الجيل-3 يجب أن يختلف عن الجيل-2”.
- أي حالة “مرتكزة صحيحة” ستقبل حتى لو لم يوجد انجراف تاريخي بين الأجيال، بشرط أن تكون النتيجة النهائية والـ raw replay صالحة.

## 4) هل يلزم تبسيط سجل الأجيال إلى تمثيل واحد؟

**الجواب: لا يلزم الآن**.

يمكن تحقيق متطلبات “الثابت الجوهري الأول” بدون دمج الأجيال:
- الاحتفاظ بـ `generation_history` كمرجع تدقيق (audit trail).
- فصله عن شرط القبول النهائي.

إذا فُرض الدمج مستقبلاً:
- يلزم تعريف مخطط `schema-6` أو تعديل `schema-5`.
- كتابة تحويل رجعي لكافة snapshots المنشورة سابقًا في:
  - `etimad-plus-viewer` data snapshots
  - نسخ النسخة الثانوية/المرآة
- تحديث المصدّرات، العقود، وملف `check_data_contract.py`.
- تحديث واجهة التوقيعات المرجعية + اختبارات ترحيل + اختبار قابلية الرجوع (rollback).
- تكلفة متوقعة: **متوسطة إلى عالية** (5–8 أيام زمنية تشغيلية مع مراجعة ملفات تاريخية كبيرة)، ومخاطر عالية على توافق الإصدارات القديمة.

## 5) مرجع تطبيقي بعد التصحيح (WO-12b)

- سبب وجود شرط “لا بد من انجراف بين الجيل 2 و3” في المرة الأولى كان **درعاً احترازياً** ضد قبول انتقالات من جيل إلى آخر دون دليل إثبات صريح لانتقال النهاية.
- الدليل الصحيح الآن هو `day_close_refresh` في حالات الانتقالات النظيفة، لذلك أصبح هذا الشرط:
  - فحصاً تشخيصياً (audit) لا يوقف القبول النهائي.
  - يُبلغ عنه في `diagnostics` كتحذير (`KASHAF_DATA_CONTRACT_WARN`).
- الاستثناءات الحاسمة ما زالت قفلية:
  - صحة الأدلة الخام (`raw_replay_valid`).
  - سلامة `sha256` و `raw_path`.
  - تناسق هويات التكرار وعدم تضارب الهوية/الدوائر.
