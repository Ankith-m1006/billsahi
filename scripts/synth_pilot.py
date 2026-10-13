import csv, json, random, re, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from gemma import generate
ROOT = pathlib.Path(__file__).resolve().parents[1]
entries = list(csv.DictReader(open(ROOT / "data/gst_goods_rates.csv", encoding="utf-8")))
random.seed(7)
pick = random.sample([e for e in entries if e["hsn_prefix"].isdigit()], 3)
PROMPT = """You write realistic line items exactly as they appear on small Indian shop bills and item lists (kirana, hardware, textile, stationery, medical, electrical).
GST notification entry (HSN {prefix}): "{desc}"
Write 12 different item names a shopkeeper or supplier would actually type for goods that fall under THIS entry. Mix: brand names, abbreviations (pkt, gm, ltr, pc, dz), Hinglish words, a few in Kannada or Hindi script, typos, pack sizes, prices. Only goods that clearly belong to this entry.
Return JSON only: [{{"item": "...", "why": "few words"}}]"""
for e in pick:
    out = generate(PROMPT.format(prefix=e["hsn_prefix"], desc=e["description"][:600]), model="gemma-4-31b-it")
    m = re.search(r"\[.*\]", out, re.S)
    items = json.loads(m.group(0)) if m else []
    print("==", e["entry_id"], e["total_rate_pct"] + "%", e["hsn_prefix"], e["description"][:100])
    for it in items: print("   ", it["item"], "|", it.get("why", ""))
