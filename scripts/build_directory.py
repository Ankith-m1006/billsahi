"""Normalise the HSN directory: pad codes to 8 digits, tidy descriptions, add 2/4/6-digit parents."""
import json, re, csv, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
rows = json.load(open(root / "data/raw/hsn_codes.json", encoding="utf-8"))
out = []
for r in rows:
    code = r["code"].strip().zfill(8)
    desc = re.sub(r"\s+", " ", r["description"]).strip()
    out.append({"code": code, "chapter": code[:2], "heading": code[:4], "subheading": code[:6], "description": desc})
codes = [o["code"] for o in out]
assert len(codes) == len(set(codes)), "duplicate codes"
with open(root / "data/hsn_directory.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader(); w.writerows(out)
print(len(out), "codes;", len({o['heading'] for o in out}), "headings;", len({o['chapter'] for o in out}), "chapters")
