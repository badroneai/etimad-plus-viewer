# مؤشر هاندوفر الجلب

هاندوفر إكمال الجلب (المستودع + النواقص + بروتوكول النافذة) موجود في مستودع الجلب:

**https://github.com/badroneai/ksa-coffee-atlas/blob/cursor/etimad-tenders-platform-a39e/etimad-platform/HANDOVER_FETCH_WINDOW.md**

هذا المستودع (`etimad-plus-viewer`) للعرض فقط. بعد أي جلب جديد:

```bash
python scripts/export_warehouse.py
git add data && git commit -m "Refresh mirror from warehouse" && git push
```

كتب المؤشر: وكيل Cursor — 2026-07-17.
