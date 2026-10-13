"""Build gst_goods_rates.csv from CBIC rate notification PDFs.

Usage:
  python -I parse_rates.py <notifications_dir> <out_csv> <out_audit_json>

Sources (all from taxinformation.cbic.gov.in, see manifest.json):
  09-2025-CTR.pdf   Notification 9/2025-CT(Rate), Schedules I-VII (rates)
  10-2025-CTR.pdf   Notification 10/2025-CT(Rate), exemption Schedule (nil)
  14-2025-CTR.pdf   Notification 14/2025-CT(Rate), bricks at 6% CGST
  19-2025-CTR.pdf   amends 9/2025 w.e.f. 01.02.2026 (tobacco)
  01-2026-CTR.pdf   amends 9/2025 w.e.f. 01.05.2026 (2202 tariff items)

Amendments are hand-encoded below from the PDF text (verbatim strings) and
each one is asserted against the extracted amendment text, so a typo here fails
the build instead of silently changing a rate.
"""
import csv
import json
import re
import sys
from pathlib import Path

import pdfplumber

SCHEDULES_9 = [  # (schedule, CGST %, expected last serial)
    ("I", 2.5, 516), ("II", 9, 640), ("III", 20, 13), ("IV", 1.5, 15),
    ("V", 0.125, 3), ("VI", 0.75, 2), ("VII", 14, 6),
]
SNO_RE = re.compile(r"^(\d+[A-Z]?)\.$")


def norm(s):
    # a hyphen at a line end is a real hyphen ("pre-<newline>packaged"): keep it, drop the break
    s = re.sub(r"-[ ]?\n(?=[a-z])", "-", s or "")
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def cells_to_triplet(row):
    """Map a pdfplumber row (3, 5 or 7 cells) to (sno, hsn, desc).

    Observed layouts: 3 = [sno, hsn, desc]; 5 = [sno, hsn, '', desc, '']
    or [sno, hsn, desc, None, None]; 7 = [sno, hsn|'', hsn|None, None|'',
    desc|'', desc|None, None|''].  Columns 1..(n//2) hold the HSN, the rest
    hold the description.
    """
    row = [c if c is not None else "" for c in row]
    if len(row) == 3:
        return row[0], row[1], row[2]
    if len(row) == 5:
        if row[2] and not row[3]:
            return row[0], row[1], row[2]
        return row[0], " ".join(x for x in row[1:3] if x), " ".join(x for x in row[3:] if x)
    if len(row) == 7:
        return row[0], " ".join(x for x in row[1:4] if x), " ".join(x for x in row[4:] if x)
    return None


def table_entries(pdf_path, audit, tag):
    """Return ordered list of dicts {sno, hsn, desc, page} from all tables."""
    entries, skipped = [], []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for t in page.extract_tables():
                for row in t:
                    trip = cells_to_triplet(row)
                    if trip is None:
                        skipped.append({"page": pno, "row": row, "why": f"{len(row)} cells"})
                        continue
                    sno, hsn, desc = (norm(x) for x in trip)
                    if sno.startswith("S. No") or sno == "(1)":
                        continue
                    m = SNO_RE.match(sno)
                    if m:
                        entries.append({"sno": m.group(1), "hsn": hsn, "desc": desc, "page": pno})
                    elif not sno and entries:
                        prev = entries[-1]
                        # spurious duplicate fragments (seen on p.46 of 9/2025)
                        if desc and prev["desc"].endswith(desc) and not hsn:
                            skipped.append({"page": pno, "row": row, "why": "duplicate fragment"})
                            continue
                        if hsn:
                            prev["hsn"] = norm(prev["hsn"] + " " + hsn)
                        if desc:
                            prev["desc"] = norm(prev["desc"] + " " + desc)
                        prev.setdefault("cont_pages", []).append(pno)
                    else:
                        skipped.append({"page": pno, "row": row, "why": "no serial"})
    audit[tag + "_skipped_rows"] = skipped
    return entries


def split_schedules(entries, plan):
    out, idx, cur = {}, -1, None
    for e in entries:
        if e["sno"] == "1":
            idx += 1
            cur = plan[idx][0]
            out[cur] = []
        out[cur].append(e)
    return out


def check_serials(entries, last):
    nums = [e["sno"] for e in entries]
    expected = [str(i) for i in range(1, last + 1)]
    return {"missing": sorted(set(expected) - set(nums), key=lambda x: int(re.sub(r"\D", "", x))),
            "extra": [n for n in nums if n not in expected],
            "dupes": sorted({n for n in nums if nums.count(n) > 1})}


# ---------------------------------------------------------------- HSN parsing
EXCL_RE = re.compile(r"[\[(]\s*(?:other than|except)\s*([^\])]*)[\])]", re.I)


def parse_hsn(raw):
    """Return (hsn_prefix list, exclusion text, any_chapter flag, notes list)."""
    s = norm(raw)
    notes = []
    excl_parts = [norm(x) for x in EXCL_RE.findall(s)]
    s = EXCL_RE.sub(" ", s)
    m = re.search(r"\bexcept\s+([\d ,]+)$", s, re.I)  # "Any Chapter except 71"
    if m:
        excl_parts.append(norm(m.group(1)))
        s = s[:m.start()]
    excl = "; ".join(excl_parts)
    if s in ("-", ""):
        return [""], excl, False, ["no HSN in column (2); applies by description"]
    any_ch = bool(re.search(r"any (other )?chapter", s, re.I))
    s = re.sub(r"\bor any (other )?chapter\b", ",", s, flags=re.I)
    s = re.sub(r"\bany (other )?chapter\b", ",", s, flags=re.I)
    codes = []
    for p in re.split(r",|/|\bor(?=\d|\b)|\band\b", s):
        p = p.strip()
        if not p:
            continue
        r = re.fullmatch(r"(\d+)\s+to\s+(\d+)", p)
        if r:
            a, b = r.groups()
            if len(a) != len(b) or int(b) - int(a) > 20:
                raise ValueError(f"odd range {p!r} in {raw!r}")
            codes += [str(n).zfill(len(a)) for n in range(int(a), int(b) + 1)]
            notes.append(f"HSN range {a} to {b} expanded")
            continue
        groups = p.split()
        if not all(g.isdigit() for g in groups):
            raise ValueError(f"unparsed HSN fragment {p!r} in {raw!r}")
        if len("".join(groups)) <= 8:
            codes.append("".join(groups))
            continue
        # missing comma in source, e.g. "2711 12 00 2711 13 00": a new code
        # starts at every 4-digit group
        cur = ""
        for g in groups:
            if len(g) == 4 and cur:
                codes.append(cur)
                cur = ""
            cur += g
        codes.append(cur)
        notes.append(f"HSN '{p}' split at 4-digit groups (no comma in source)")
    if any_ch:
        codes.append("ANY")
    return codes, excl, any_ch, notes


COND_PATTERNS = [
    (r"pre-?packaged and labelled", "pre-packaged and labelled"),
    (r"\bbrand", "branding"),
    (r"unit container", "unit container"),
    (r"other than|excluding|\bexcept\b|\[other", "'other than'/exclusion"),
    (r"not exceeding|exceeding|more than|less than|not more than", "threshold (value/size/capacity)"),
    (r"\bRs\.?\s?\d|sale value|per piece|per pair", "value threshold"),
    (r"supplied to|supplied by|for supply|supply to|supply by|domestic consumers|cleared as|for use|used (in|for|by)|intended for|meant for|for the manufacture|"
     r"when supplied|for personal use|for blending|by the disabled|for disabled", "end use / supplier"),
    (r"\bcc\b|\bmm\b|\d+\s?cc", "engine/length spec"),
]


def conditions(desc, excl, any_ch):
    notes = []
    for pat, label in COND_PATTERNS:
        if re.search(pat, desc, re.I) and label not in notes:
            notes.append(label)
    if excl:
        notes.append(f"HSN excludes: {excl}")
    if any_ch:
        notes.append("any chapter: classification by description, not HSN")
    return bool(notes), "; ".join(notes)


# --------------------------------------------------------------- amendments
def must_contain(text, *snips):
    t = norm(text)
    for s in snips:
        if norm(s) not in t:
            raise SystemExit(f"amendment text check failed: {s!r}")


def apply_amendments(sch, ndir, audit):
    t19 = (Path(ndir).parent / "notifications_text" / "19-2025-CTR.txt").read_text(encoding="utf-8")
    t01 = (Path(ndir).parent / "notifications_text" / "01-2026-CTR.txt").read_text(encoding="utf-8")
    log = []

    # 19/2025-CT(Rate) dated 31.12.2025, in force 01.02.2026
    must_contain(t19, "in the Schedule II – 9%, after S. No. 4", "4A. 2403 19 21, 2403 19 Biris")
    idx = [e["sno"] for e in sch["II"]].index("4") + 1
    sch["II"].insert(idx, {"sno": "4A", "hsn": "2403 19 21, 2403 19 29", "desc": "Biris",
                           "page": None, "amended_by": "19/2025 (ins. w.e.f. 01.02.2026)"})
    log.append("19/2025: inserted Schedule II S.No. 4A (Biris, 2403 19 21, 2403 19 29)")
    must_contain(t19, "in the Schedule III – 20%, after S. No. 13")
    new3 = [
        ("14", "2106 90 20", "Pan masala"),
        ("15", "2401", "Unmanufactured tobacco; tobacco refuse [other than tobacco leaves]"),
        ("16", "2402", "Cigars, cheroots, cigarillos and cigarettes, of tobacco or of tobacco substitutes"),
        ("17", "2403 (other than 2403 19 21, 2403 19 29)",
         "Other manufactured tobacco and manufactured tobacco substitutes; homogenised or reconstituted "
         "tobacco; tobacco extracts and essences [other than biris]"),
        ("18", "2404 11 00", "Products containing tobacco or reconstituted tobacco and intended for "
                             "inhalation without combustion"),
        ("19", "2404 19 00", "Products containing tobacco or nicotine substitutes and intended for "
                             "inhalation without combustion"),
    ]
    for sno, hsn, desc in new3:
        must_contain(t19, desc.split(";")[0][:40])
        sch["III"].append({"sno": sno, "hsn": hsn, "desc": desc, "page": None,
                           "amended_by": "19/2025 (ins. w.e.f. 01.02.2026)"})
    log.append("19/2025: inserted Schedule III S.Nos. 14-19 (pan masala, tobacco)")
    must_contain(t19, "the Schedule VII – 14%, and the entries relating thereto shall be omitted")
    audit["omitted_schedule_VII"] = sch.pop("VII")
    log.append("19/2025: omitted Schedule VII (14%) entirely")

    # Corrigendum dated 18.09.2025 to 9/2025: gazette p.88 lines 9-16, list
    # labels "b."..."i." read "a."..."h." (only entry with that run: Sch I S.No. 292)
    tcor = (Path(ndir).parent / "notifications_text" / "09-2025-CTR-corrigendum.txt").read_text(encoding="utf-8")
    must_contain(tcor, "at page 88, line 9, for “b.”, read “a.”", "at page 88, line 16, for “i.”, read “h.”")
    e = next(x for x in sch["I"] if x["sno"] == "292")
    assert e["desc"].startswith("The following goods, namely: — b. Cement Bonded"), e["desc"][:60]
    d = e["desc"]
    for old_l, new_l in zip("bcdefghi", "abcdefgh"):
        d = re.sub(rf"(?<![A-Za-z]){old_l}\. (?=[A-Z])", f"@@{new_l}. ", d, count=1)
    e["desc"] = d.replace("@@", "")
    e["amended_by"] = "Corrigendum 18.09.2025 (list labels b-i read a-h)"
    log.append("Corrigendum 18.09.2025: Schedule I S.No. 292 list relabelled a.-h.")

    # 01/2026-CT(Rate) dated 30.04.2026, in force 01.05.2026
    subs = [("I", "150", "2202 99 21, 2202 99 29"), ("I", "151", "22029931, 22029939"),
            ("III", "2", "2202 91 00, 2202 99 91, 2202 99 99"), ("III", "3", "2202 99 91, 2202 99 99")]
    must_contain(t01, "This notification shall come into force from 1st May, 2026")
    for s, sno, new in subs:
        must_contain(t01, f"S. No. {sno}, for the entry in column (2), the entry “{new}”")
        e = next(x for x in sch[s] if x["sno"] == sno)
        log.append(f"01/2026: Schedule {s} S.No. {sno} column (2) {e['hsn']!r} -> {new!r}")
        e["hsn_original"], e["hsn"] = e["hsn"], new
        e["amended_by"] = "01/2026 (col.2 subst. w.e.f. 01.05.2026)"
    audit["amendments_applied"] = log


# --------------------------------------------------------------------- main
def text_check(entries, txt):
    """Confirm each entry's serial+description start appears in the page text."""
    t = norm(txt)
    bad = []
    for e in entries:
        if e.get("page") is None:
            continue
        head = norm(e["desc"])[:25]
        if f"{e['sno']}." not in t or head not in t:
            bad.append({"sno": e["sno"], "desc_head": head})
    return bad


def main(ndir, out_csv, out_audit):
    ndir = Path(ndir)
    tdir = ndir.parent / "notifications_text"
    audit = {}

    e9 = table_entries(ndir / "09-2025-CTR.pdf", audit, "9_2025")
    sch = split_schedules(e9, SCHEDULES_9)
    audit["9_2025_serial_check"] = {s: check_serials(sch[s], last) for s, _, last in SCHEDULES_9}
    audit["9_2025_text_mismatch"] = text_check(e9, (tdir / "09-2025-CTR.txt").read_text(encoding="utf-8"))

    e10 = table_entries(ndir / "10-2025-CTR.pdf", audit, "10_2025")
    audit["10_2025_count"] = len(e10)
    last10 = max(int(e["sno"]) for e in e10 if e["sno"].isdigit())
    audit["10_2025_serial_check"] = check_serials(e10, last10)
    audit["10_2025_text_mismatch"] = text_check(e10, (tdir / "10-2025-CTR.txt").read_text(encoding="utf-8"))

    e14 = table_entries(ndir / "14-2025-CTR.pdf", audit, "14_2025")

    apply_amendments(sch, ndir, audit)

    rate = {s: r for s, r, _ in SCHEDULES_9}
    rows, errors = [], []

    def emit(entry_id, schedule, total, e, source):
        try:
            codes, excl, any_ch, hnotes = parse_hsn(e["hsn"])
        except ValueError as ex:
            errors.append({"entry_id": entry_id, "error": str(ex)})
            return
        has, note = conditions(e["desc"], excl, any_ch)
        if hnotes:
            note = "; ".join([note] + hnotes if note else hnotes)
            if codes == [""]:
                has = True
        for c in codes:
            rows.append({
                "entry_id": entry_id, "schedule": schedule,
                "total_rate_pct": f"{total:g}", "hsn_prefix": c,
                "description": e["desc"], "has_condition": str(has).lower(),
                "condition_note": note, "source_notification": source,
                "amended_by": e.get("amended_by", ""),
            })

    src9 = "9/2025-CT(Rate) dt 17.09.2025"
    for s in ("I", "II", "III", "IV", "V", "VI"):
        for e in sch[s]:
            emit(f"9/2025-S{['I','II','III','IV','V','VI'].index(s)+1}-{e['sno']}", s, rate[s] * 2, e, src9)
    for e in e10:
        emit(f"10/2025-EX-{e['sno']}", "Exempt", 0, e, "10/2025-CT(Rate) dt 17.09.2025")
    for e in e14:
        e = dict(e, amended_by="")
        emit(f"14/2025-S-{e['sno']}", "14/2025", 12, e, "14/2025-CT(Rate) dt 17.09.2025")

    # bricks: 14/2025 is a separate notification; flag every row
    for r in rows:
        if r["schedule"] == "14/2025":
            r["has_condition"] = "true"
            r["condition_note"] = (r["condition_note"] + "; " if r["condition_note"] else "") + \
                "separate bricks notification 14/2025 (12%); 9/2025 also lists headings 6815/6901/6904/6905 for other goods"

    # Overlap flags: an entry whose hsn_prefix is equal to, a prefix of, or
    # extended by another entry's prefix at a DIFFERENT rate competes with it
    # (e.g. 6403 at 18% vs chapter 64 footwear <= Rs 2500/pair at 5%).  The
    # most-specific-prefix rule alone would silently pick one.  All such
    # overlaps are listed in competing_entries; an otherwise-unconditional row
    # is additionally flagged has_condition=true when a competitor is itself
    # conditional (the footwear case), because then the specific match may be
    # wrong.  Unconditional-vs-unconditional overlaps are left to the
    # most-specific rule, which is how the tariff is meant to be read.
    by_rate = [(r["hsn_prefix"], r["total_rate_pct"], r["entry_id"], r["has_condition"] == "true")
               for r in rows if r["hsn_prefix"] not in ("", "ANY")]
    flagged_by_overlap = 0
    for r in rows:
        h = r["hsn_prefix"]
        comp, cond_comp = set(), set()
        if h not in ("", "ANY"):
            for h2, rate2, eid2, cond2 in by_rate:
                if eid2 != r["entry_id"] and rate2 != r["total_rate_pct"] and (h.startswith(h2) or h2.startswith(h)):
                    comp.add(f"{eid2}@{rate2}%")
                    if cond2 and len(h2) <= len(h):  # broader/equal conditional entry may carve this out
                        cond_comp.add(f"{eid2}@{rate2}%")
        r["competing_entries"] = " ".join(sorted(comp))
        if cond_comp and r["has_condition"] != "true":
            flagged_by_overlap += 1
            r["has_condition"] = "true"
            shown = " ".join(sorted(cond_comp)[:3]) + (" ..." if len(cond_comp) > 3 else "")
            r["condition_note"] = f"no condition in own text, but competes with conditional entry {shown}"
    audit["rows_with_competing_entries"] = sum(1 for r in rows if r["competing_entries"])
    audit["rows_flagged_only_by_overlap"] = flagged_by_overlap

    audit["parse_errors"] = errors
    audit["row_counts"] = {}
    for r in rows:
        audit["row_counts"][r["schedule"]] = audit["row_counts"].get(r["schedule"], 0) + 1
    audit["entry_counts"] = {s: len(v) for s, v in sch.items()}
    audit["entry_counts"]["Exempt"] = len(e10)
    audit["entry_counts"]["14/2025"] = len(e14)

    fields = ["entry_id", "schedule", "total_rate_pct", "hsn_prefix", "description",
              "has_condition", "condition_note", "source_notification", "amended_by", "competing_entries"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    Path(out_audit).write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ("entry_counts", "row_counts", "parse_errors",
                                             "9_2025_serial_check", "10_2025_serial_check")}, indent=1))
    print("text mismatches:", len(audit["9_2025_text_mismatch"]), len(audit["10_2025_text_mismatch"]))
    print("skipped rows:", len(audit["9_2025_skipped_rows"]), len(audit["10_2025_skipped_rows"]))


if __name__ == "__main__":
    main(*sys.argv[1:4])
