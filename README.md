# كشّاف — مرصد منافسات اعتماد بلس

واجهة عربية ثابتة لعرض بيانات المنافسات الحكومية المجلوبة من مستودع **اعتماد بلس** (مجمّع غير رسمي). ليست بديلاً عن [منصة اعتماد](https://tenders.etimad.sa).

## المعاينة

بعد تفعيل GitHub Pages:

**https://badroneai.github.io/etimad-plus-viewer/**

## ماذا يعرض

| المجموعة | الحالة |
|---------|--------|
| المنافسات المفتوحة | 1,604 |
| خلال 7 / 30 يوماً | 782 / 1,580 |
| المرساة | 11,300 (جزئي) |
| الجهات / الأنشطة / الأنواع | 802 / 138 / 9 |
| شركات (SSR) | 200 |

## التشغيل محلياً

يحتاج خادم HTTP بسيط (لا يعمل `file://` مع `fetch`):

```bash
npx serve .
# أو
python -m http.server 8080
```

ثم افتح `http://localhost:8080`.

## البنية

```
index.html
assets/styles.css
assets/app.js
data/
  manifest.json
  open.json
  within_7.json
  within_30.json
  awarded.json
  agencies.json
  activities.json
  types.json
  companies.json
```

## ملاحظة

البيانات لقطة استكشافية شخصية. المصدر الرسمي للمنافسات هو tenders.etimad.sa.
