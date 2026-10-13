"""Step 3: turn verified synthetic items into the two chat tasks the pipeline asks the model.

  heading task: item + plain-search shortlist -> {"heading": "dddd"}
  entry task:   item + official entries for that heading -> {"entry": "<id>"}   (only when >1 entry)

Prompts come from app/pipeline.py, so training and inference see the same text.
Split by item (dev 5%), seeded; the real shop test set is never in here.
"""
import json, os, random, sys, pathlib
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import pipeline

SRC = pathlib.Path(os.environ.get("SFT_SRC", ROOT / "data/synth/verified.jsonl"))
OUT = pathlib.Path(os.environ.get("SFT_OUT", ROOT / "data/sft"))
OUT.mkdir(exist_ok=True)


def rows():
    seen = set()
    for l in open(SRC, encoding="utf-8"):
        r = json.loads(l)
        k = (r["item"].lower(), r["entry_id"])
        if r["keep"] and k not in seen:
            seen.add(k)
            yield r


_entries_cache = {}


def build(r):
    cands = pipeline.search_headings(r["item"])
    convs = [pipeline.heading_messages(r["item"], cands) + [{"role": "assistant", "content": json.dumps({"heading": r["heading"]})}]]
    h = r["heading"]
    if h not in _entries_cache:
        _entries_cache[h] = pipeline.entries_for_heading(h)
    entries = _entries_cache[h]
    if len(entries) > 1 and any(e["entry_id"] == r["entry_id"] for e in entries):
        convs.append(pipeline.entry_messages(r["item"], h, entries) + [{"role": "assistant", "content": json.dumps({"entry": r["entry_id"]})}])
    return r["item"], convs


CACHE = OUT / "_built_cache.jsonl"


if __name__ == "__main__":
    import threading
    items = list(rows())
    random.seed(5)
    random.shuffle(items)
    n_dev = max(50, len(items) // 20)
    cache = {}
    if CACHE.exists():
        for l in open(CACHE, encoding="utf-8"):
            c = json.loads(l)
            cache[c["key"]] = c["convs"]
    lock = threading.Lock()

    def key(r):
        return r["item"].lower() + "|" + r["entry_id"]

    def build_cached(r):
        k = key(r)
        if k in cache:
            return r["item"], cache[k]
        item, convs = build(r)
        with lock, open(CACHE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": k, "convs": convs}, ensure_ascii=False) + "\n")
        return item, convs

    print(len(items), "items,", len(cache), "cached", flush=True)
    with ThreadPoolExecutor(16) as ex:
        built = list(ex.map(build_cached, items))
    for name, part in (("dev", built[:n_dev]), ("train", built[n_dev:])):
        with open(OUT / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for item, convs in part:
                for c in convs:
                    f.write(json.dumps({"item": item, "messages": c}, ensure_ascii=False) + "\n")
        print(name, len(part), "items")
