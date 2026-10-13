"""Step 1 of the training data: Gemma 4 writes shop-style item names for each notification entry.

Resumable: one JSON line per entry in data/synth/raw_items.jsonl. Labels here are only
"proposed"; scripts/synth_verify.py keeps an item only if a blind second pass agrees.
"""
import csv, json, os, re, sys, pathlib, threading, zlib
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from gemma import generate

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(os.environ.get("GEN_OUT", ROOT / "data/synth/raw_items.jsonl"))
CHAPTERS = set(filter(None, os.environ.get("GEN_CHAPTERS", "").split(",")))
N_ITEMS = int(os.environ.get("GEN_N", "12"))

PROMPT = """You write realistic line items exactly as they appear on small Indian shop bills and item lists (kirana, hardware, textile, stationery, medical, electrical, footwear, bakery).
GST notification entry (HSN {prefixes}): "{desc}"
Write {n} different item names a shopkeeper or supplier would actually type for goods that fall under THIS entry and nowhere else.{retail} Mix: brand names, abbreviations (pkt, gm, ltr, pc, dz, nos), Hinglish words, two or three in Kannada or Hindi script, typos, pack sizes, prices. Do not include goods that belong to a different HSN heading. If the entry is too technical for a small shop, write what a wholesaler's bill would say.
For each item give the 4-digit HSN heading it falls under (one of: {headings}).
Return JSON only: [{{"item": "...", "heading": "...."}}]"""


def entries():
    by_id = {}
    for r in csv.DictReader(open(ROOT / "data/gst_goods_rates.csv", encoding="utf-8")):
        p = r["hsn_prefix"].strip()
        if not p.isdigit():
            continue
        e = by_id.setdefault(r["entry_id"], {"entry_id": r["entry_id"], "rate": r["total_rate_pct"], "description": r["description"], "prefixes": []})
        e["prefixes"].append(p)
    return list(by_id.values())


def headings_for(prefixes, directory):
    hs = sorted({h for p in prefixes for h in directory if h.startswith(p[:4]) or p.startswith(h) or h.startswith(p)})
    return hs[:60] or sorted({p[:4] for p in prefixes})


lock = threading.Lock()
QWEN = "Qwen/Qwen3.5-397B-A17B"
RETAIL = (" They appear on a kirana, general store, hardware, stationery, medical, cloth or footwear shop's bill. At least half must be real Indian brand or product names written the way bills write them (for example Hide & Seek, Rin, Pril, Anchor, Doms, Polycab, Paragon, Liberty), often without saying what the product is. For clothing, made-up textiles and footwear include a price in rupees, sometimes above and sometimes below 2500, matching this entry.") if os.environ.get("GEN_STYLE") == "retail" else ""
MODELS = os.environ.get("GEN_MODELS", "gemma-4-31b-it,gemma-4-26b-a4b-it").split(",")


def pick_model(entry_id):
    """Stable across runs (crc32, not Python's per-process hash)."""
    return MODELS[zlib.crc32(entry_id.encode()) % len(MODELS)]


_tinker = None


def tinker_generate(prompt):
    global _tinker
    if _tinker is None:
        sys.path.insert(0, str(ROOT / "app"))
        import llms
        _tinker = llms.tinker_model(base_model=QWEN, max_tokens=1500)
    return _tinker([{"role": "user", "content": prompt}])


def work(e, directory, done):
    if e["entry_id"] in done:
        return
    hs = headings_for(e["prefixes"], directory)
    prompt = PROMPT.format(prefixes=", ".join(e["prefixes"]), desc=e["description"][:900], n=N_ITEMS, headings=", ".join(hs), retail=RETAIL)
    try:
        model = pick_model(e["entry_id"])
        out = tinker_generate(prompt) if model == QWEN else generate(prompt, model=model)
        m = re.search(r"\[.*\]", out, re.S)
        items = json.loads(m.group(0)) if m else []
        items = [i for i in items if isinstance(i, dict) and i.get("item") and str(i.get("heading", "")) in hs]
    except Exception as ex:
        print("ERR", e["entry_id"], repr(ex)[:120], flush=True)
        return
    with lock:
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps({**e, "gen_model": model, "items": items}, ensure_ascii=False) + "\n")
    print(e["entry_id"], len(items), flush=True)


if __name__ == "__main__":
    directory = sorted({r["heading"] for r in csv.DictReader(open(ROOT / "data/hsn_directory.csv", encoding="utf-8"))})
    done = set()
    if OUT.exists():
        done = {json.loads(l)["entry_id"] for l in open(OUT, encoding="utf-8")}
    todo = [e for e in entries() if not CHAPTERS or any(p[:2] in CHAPTERS for p in e["prefixes"])]
    print(len(todo), "entries,", len(done), "already done", flush=True)
    with ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 6) as ex:
        for e in todo:
            ex.submit(work, e, directory, done)
