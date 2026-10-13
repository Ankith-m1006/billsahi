"""Step 2: drop a generated item if a blind pass by the *other* Gemma 4 model puts it in a different chapter.

Pilot (25 items): exact heading agreement was 11/25, but most disagreements were near-misses where the
blind pass was the wrong one (groundnut oil 1508 vs 1507). Cross-chapter ones were real label errors
(granite tiles 2516 vs 6802, a Hep-B test kit 3002 vs 3822), so chapter disagreement is the drop rule.

Items are shuffled across entries and sent 25 at a time with no hint of the intended heading.
Output: data/synth/verified.jsonl (one item per line: item, heading, entry_id, rate, gen_model, blind_heading, keep).
"""
import hashlib, json, os, random, re, sys, pathlib, threading
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from gemma import generate

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = pathlib.Path(os.environ.get("VER_RAW", ROOT / "data/synth/raw_items.jsonl"))
OUT = pathlib.Path(os.environ.get("VER_OUT", ROOT / "data/synth/verified.jsonl"))

PROMPT = """For each item from an Indian shop bill below, give the 4-digit HSN heading (Indian Customs Tariff / GST) it falls under. Item names may be brands, abbreviations, Hinglish, Hindi or Kannada.
Return JSON only, one object per item in the same order: [{{"n": 1, "heading": "dddd"}}, ...]

{lines}"""


def gen_model(entry):
    """Recorded by the generator since 13 Oct midday; older lines did not record it (unknown)."""
    return entry.get("gen_model", "unknown")


def key(it):
    return hashlib.sha1((it["entry_id"] + "|" + it["item"]).encode()).hexdigest()[:16]


lock = threading.Lock()
from synth_generate import QWEN, tinker_generate


def work(batch, model):
    lines = "\n".join(f"{i+1}. {it['item']}" for i, it in enumerate(batch))
    try:
        out = tinker_generate(PROMPT.format(lines=lines)) if model == QWEN else generate(PROMPT.format(lines=lines), model=model, temperature=0.0)
        m = re.search(r"\[.*\]", out, re.S)
        ans = {int(a["n"]): str(a.get("heading", "")) for a in json.loads(m.group(0))} if m else {}
    except Exception as ex:
        print("ERR", repr(ex)[:120], flush=True)
        return
    with lock, open(OUT, "a", encoding="utf-8") as f:
        for i, it in enumerate(batch):
            b = ans.get(i + 1, "")
            f.write(json.dumps({**it, "blind_model": model, "blind_heading": b, "agree": b == it["heading"], "keep": b[:2] == it["heading"][:2]}, ensure_ascii=False) + "\n")
    print(model, len(batch), sum(ans.get(i + 1) == it["heading"] for i, it in enumerate(batch)), flush=True)


if __name__ == "__main__":
    done = {json.loads(l)["key"] for l in open(OUT, encoding="utf-8")} if OUT.exists() else set()
    items = []
    for l in open(RAW, encoding="utf-8"):
        e = json.loads(l)
        for it in e["items"]:
            row = {"item": it["item"].strip(), "heading": str(it["heading"]), "entry_id": e["entry_id"], "rate": e["rate"], "gen_model": gen_model(e)}
            row["key"] = key(row)
            if row["key"] not in done:
                items.append(row)
    random.seed(11)
    random.shuffle(items)
    # blind pass uses the model that did NOT write the item
    # Gemma-written (incl. older unrecorded lines, all Gemma) -> Qwen 397B on Tinker; Qwen-written -> Gemma 4 31B
    by_model = {QWEN: [], "gemma-4-31b-it": []}
    for it in items:
        forced = os.environ.get("VER_MODEL")
        by_model.setdefault(forced, []) if forced else None
        by_model[forced or ("gemma-4-31b-it" if it["gen_model"] == QWEN else QWEN)].append(it)
    print(len(items), "items to verify", flush=True)
    with ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 8) as ex:
        for model, its in by_model.items():
            for i in range(0, len(its), 25):
                ex.submit(work, its[i:i + 25], model)
