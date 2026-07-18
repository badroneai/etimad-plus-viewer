# مزامنة كشاف — بعد جلب الماك

آخر تصدير من مستودع الجلب (`awarded=50000` جزئي، `winnerfacet=300` جزئي).

- المصدر: `ksa-coffee-atlas` فرع `cursor/etimad-tenders-platform-a39e`
- راجع أيضاً: `etimad-platform/CROSS_DEVICE_SYNC.md` هناك
- جديد في الواجهة: فلتر **المنطقة** في المستكشف

```bash
python scripts/export_warehouse.py   # بعد أي جلب جديد
python -m http.server 8080
```

Pages: https://badroneai.github.io/etimad-plus-viewer/
