"""Sanity checks on gst_goods_rates.csv against the extracted notification text.

Usage: python -I verify_rates.py <csv> <text_dir>
1. hsn_prefix lengths (expect 2/4/6/8 digits, 'ANY' or '').
2. word-bag fidelity: words of each schedule's descriptions vs. words in the
   notification text for that schedule's span (reports words in CSV that are
   not in the text, which would indicate invented/garbled text).
"""
import csv
import re
import sys
from collections import Counter
from pathlib import Path


def words(s):
    return re.findall(r"[A-Za-z]{3,}", s.lower())


def main(csv_path, tdir):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    odd = Counter((r["entry_id"], r["hsn_prefix"]) for r in rows
                  if r["hsn_prefix"] not in ("ANY", "") and len(r["hsn_prefix"]) not in (2, 4, 6, 8))
    print("odd-length hsn:", dict(odd) or "none")
    texts = {"9/2025": Path(tdir, "09-2025-CTR.txt").read_text(encoding="utf-8"),
             "10/2025": Path(tdir, "10-2025-CTR.txt").read_text(encoding="utf-8"),
             "14/2025": Path(tdir, "14-2025-CTR.txt").read_text(encoding="utf-8"),
             "19/2025": Path(tdir, "19-2025-CTR.txt").read_text(encoding="utf-8")}
    seen = set()
    for r in rows:
        if r["entry_id"] in seen:
            continue
        seen.add(r["entry_id"])
        src = r["source_notification"].split("-")[0]
        tw = set(words(texts[src])) | (set(words(texts["19/2025"])) if r["amended_by"] else set())
        missing = [w for w in words(r["description"]) if w not in tw]
        if missing:
            print("WORDS NOT IN SOURCE TEXT", r["entry_id"], missing)
    # span check: the text between serial N and serial N+1 of a schedule must
    # contain exactly the description's words, plus only HSN-column words.
    hsn_words = Counter({"chapter": 99, "any": 99, "other": 99, "or": 99, "to": 99,
                         "except": 99, "than": 99})
    heads9 = [("I", r"^Schedule I – 2\.5%"), ("II", r"^Schedule II – 9 %"), ("III", r"^Schedule III– 20%"),
              ("IV", r"^Schedule IV– 1\.5%"), ("V", r"^Schedule V – 0\.125%"), ("VI", r"^Schedule VI – 0\.75%"),
              ("VII", r"^Schedule VII – 14%"), ("END", r"^Explanation\.— For the purposes of this notification")]
    t9 = texts["9/2025"]
    segs = {}
    pos = [(name, re.search(p, t9, re.M).start()) for name, p in heads9]
    for (name, a), (_, b) in zip(pos, pos[1:]):
        segs[("9/2025", name)] = t9[a:b]
    t10 = texts["10/2025"]
    segs[("10/2025", "Exempt")] = t10[t10.index("SCHEDULE"):t10.index("Explanation.- For the purposes of this Schedule")]

    def spans(seg):
        seg = re.sub(r"==== PAGE \d+ ====", "", seg)
        out, n, idx = {}, 1, 0
        starts = []
        while True:
            m = re.search(rf"^{n}\. ", seg[idx:], re.M)
            if not m:
                break
            starts.append((str(n), idx + m.start()))
            idx += m.end()
            n += 1
        for (k, a), (_, b) in zip(starts, starts[1:] + [("", len(seg))]):
            out[k] = seg[a:b]
        return out

    def wbag(s):
        return Counter(re.findall(r"[a-z]{2,}", s.lower()))

    issues = 0
    seen = set()
    for (src, sch), seg in segs.items():
        sp = spans(seg)
        for r in rows:
            if r["entry_id"] in seen or not r["source_notification"].startswith(src) or r["schedule"] != sch:
                continue
            seen.add(r["entry_id"])
            sno = r["entry_id"].rsplit("-", 1)[1]
            if sno not in sp:
                if not r["amended_by"].startswith("19/2025"):
                    print("NO TEXT SPAN", r["entry_id"]); issues += 1
                continue
            d, s = wbag(r["description"]), wbag(sp[sno])
            extra = d - s
            lost = s - d - hsn_words
            if r["amended_by"].startswith("Corrigendum"):
                extra = Counter()
            if extra or lost:
                issues += 1
                print("SPAN MISMATCH", r["entry_id"], "csv-only:", dict(extra), "text-only:", dict(lost))
    print("span issues:", issues, "entries checked:", len(seen))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
