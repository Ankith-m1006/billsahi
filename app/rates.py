"""Official GST rate for an HSN code, decided by code from the CBIC notifications.

The model never states a rate. It picks a code; this module finds every notification
entry that covers that code. If they all agree, the rate is certain. If they disagree
(soap 5% vs 18%, footwear above/below Rs 2,500), we say so and show the options
instead of guessing.
"""
import csv
import pathlib
from dataclasses import dataclass, field

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"


@dataclass
class Entry:
    entry_id: str
    rate: float
    prefix: str
    description: str
    condition: str


@dataclass
class RateAnswer:
    code: str
    status: str  # "certain" | "depends" | "not_listed"
    rate: float | None
    options: list[Entry] = field(default_factory=list)
    reason: str = ""


def _load() -> dict[str, list[Entry]]:
    by_prefix: dict[str, list[Entry]] = {}
    with open(DATA / "gst_goods_rates.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = r["hsn_prefix"].strip()
            if not p.isdigit():
                continue
            note = r["condition_note"] if r["has_condition"] == "true" else ""
            e = Entry(r["entry_id"], float(r["total_rate_pct"]), p, r["description"], note)
            by_prefix.setdefault(p, []).append(e)
    return by_prefix


_BY_PREFIX = _load()


def covering_entries(code: str) -> list[Entry]:
    """Every entry whose HSN prefix covers this code, most specific first."""
    code = code.strip().zfill(8)
    hits = [e for n in range(len(code), 1, -1) for e in _BY_PREFIX.get(code[:n], [])]
    return hits


def rate_for(code: str) -> RateAnswer:
    code = code.strip().zfill(8)
    hits = covering_entries(code)
    if not hits:
        # Goods not named in any schedule fall to the residual 18% entry, but we
        # do not assert that silently; the accountant should confirm.
        return RateAnswer(code, "not_listed", None, [], "No rate entry names this code; usually the 18% residual entry, confirm with your accountant.")
    rates = sorted({e.rate for e in hits})
    if len(rates) == 1:
        return RateAnswer(code, "certain", rates[0], hits, "Every entry covering this code gives the same rate.")
    return RateAnswer(code, "depends", None, hits, "Entries covering this code give different rates: " + ", ".join(f"{r:g}%" for r in rates) + ". It depends on the item (see each entry).")


if __name__ == "__main__":
    import sys
    for c in sys.argv[1:] or ["19053100", "34011190", "64039990", "04059020", "25232930"]:
        a = rate_for(c)
        print(c, a.status, a.rate, "|", a.reason)
        for e in a.options[:4]:
            print("   ", e.entry_id, f"{e.rate:g}%", e.prefix, e.description[:90], "|", e.condition)
