"""Score every approach on a frozen test set. Every number in the post comes from a file this writes.

Test set CSV columns: item, heading (4 digits), rate (true GST %, the answer that matters),
entry_id (optional, the official entry), note (optional).
Usage: eval.py <test.csv> <system> [<system> ...]
Systems: search | gemma31 | gemma26 | gemini-flash | qwen-base | tinker:<sampler_path>
Writes runs/eval/<test-name>__<system>.jsonl (one line per item) and prints the summary table.
"""
import csv, json, sys, time, pathlib
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import pipeline, llms
from db import conn

OUT = ROOT / "runs/eval"
OUT.mkdir(parents=True, exist_ok=True)


def search_only(item):
    """Plain search baseline: the top BM25 official entry decides heading and rate. No model."""
    q = pipeline._query_text(item)
    r = pipeline.Result(item)
    if q:
        with conn() as c:
            row = c.execute("select entry_id, rate, coalesce(heading, hsn_prefix) from rate_entry "
                            "order by description <@> to_bm25query(%s, 'rate_entry_bm25') limit 1", (q,)).fetchone()
        if row:
            r.entry_id, r.rate, r.heading, r.status = row[0], float(row[1]), row[2][:4], "certain"
    return r


def backend(name):
    if name == "search":
        return None
    if name == "gemma31":
        return llms.ai_studio("gemma-4-31b-it")
    if name == "gemma26":
        return llms.ai_studio("gemma-4-26b-a4b-it")
    if name == "gemini-flash":
        return llms.ai_studio("gemini-2.5-flash")
    if name == "qwen-base":
        return llms.tinker_model()
    if name.startswith("tinker:"):
        return llms.tinker_model(model_path=name[len("tinker:"):])
    raise SystemExit("unknown system " + name)


def score(rows):
    n = len(rows)
    def pct(k): return f"{100 * sum(r[k] for r in rows) / n:.0f}%"
    wrong_confident = sum(1 for r in rows if r["status"] == "certain" and not r["rate_ok"])
    asked = sum(1 for r in rows if r["status"] == "ask")
    lat = sorted(r["seconds"] for r in rows)
    return {"n": n, "heading_ok": pct("heading_ok"), "rate_ok": pct("rate_ok"),
            "wrong_rate_stated": f"{100 * wrong_confident / n:.0f}%", "asked": f"{100 * asked / n:.0f}%",
            "median_s": round(lat[n // 2], 1)}


def run(test_path, system):
    test = list(csv.DictReader(open(test_path, encoding="utf-8")))
    llm = backend(system)
    tag = system.split("/")[-1].replace(":", "-")[:60]
    out = OUT / f"{pathlib.Path(test_path).stem}__{tag}.jsonl"

    def one(t):
        t0 = time.time()
        try:
            r = search_only(t["item"]) if llm is None else pipeline.classify(t["item"], llm)
        except Exception as ex:
            r = pipeline.Result(t["item"]); r.status = "error"; r.raw = [repr(ex)[:200]]
        sec = time.time() - t0
        return {"item": t["item"], "true_heading": t["heading"], "true_rate": float(t["rate"]),
                "heading": r.heading, "entry_id": r.entry_id, "rate": r.rate, "status": r.status, "ask": r.ask,
                "heading_ok": r.heading == t["heading"],
                "rate_ok": r.rate is not None and abs(r.rate - float(t["rate"])) < 1e-6,
                "seconds": round(sec, 2), "raw": r.raw}

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(one, test))
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return score(rows)


if __name__ == "__main__":
    test_path, systems = sys.argv[1], sys.argv[2:]
    print("| system | n | heading right | rate right | wrong rate stated | asked | median s |")
    print("|---|---|---|---|---|---|---|")
    for s in systems:
        m = run(test_path, s)
        print(f"| {s} | {m['n']} | {m['heading_ok']} | {m['rate_ok']} | {m['wrong_rate_stated']} | {m['asked']} | {m['median_s']} |", flush=True)
