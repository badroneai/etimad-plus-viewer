"""Detect English open tenders and add name_ar via Google Translate."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from deep_translator import GoogleTranslator

DATA = Path(__file__).resolve().parents[1] / "data" / "open.json"
CACHE = Path(__file__).resolve().parents[1] / "data" / "open_name_ar_cache.json"


def is_english_heavy(text: str) -> bool:
    if not text:
        return False
    letters = re.findall(r"[A-Za-z\u0600-\u06FF]", text)
    if not letters:
        return False
    latin = sum(1 for c in letters if ("A" <= c <= "Z") or ("a" <= c <= "z"))
    return (latin / len(letters)) >= 0.55


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    rows = data["records"]
    cache = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))

    translator = GoogleTranslator(source="en", target="ar")
    targets = [r for r in rows if is_english_heavy(r.get("name") or "")]
    print(f"english candidates: {len(targets)}")

    done = 0
    for i, row in enumerate(targets, 1):
        name = row.get("name") or ""
        ref = row.get("ref")
        if row.get("name_ar") and row.get("name_en"):
            done += 1
            continue
        if ref in cache and cache[ref].get("name_ar"):
            row["name_en"] = cache[ref].get("name_en") or name
            row["name_ar"] = cache[ref]["name_ar"]
            done += 1
            continue
        try:
            # Google translate limit ~5000 chars; names are short
            ar = translator.translate(name[:4500])
            time.sleep(0.12)
        except Exception as e:
            print(f"FAIL {ref}: {e}")
            continue
        row["name_en"] = name
        row["name_ar"] = ar
        cache[ref] = {"name_en": name, "name_ar": ar}
        done += 1
        if i % 25 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  progress {i}/{len(targets)}")

    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    DATA.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    with_ar = sum(1 for r in rows if r.get("name_ar"))
    print(f"done. rows_with_name_ar={with_ar}")


if __name__ == "__main__":
    main()
