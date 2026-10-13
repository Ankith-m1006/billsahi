"""Item name -> 4-digit HSN heading -> official rate entry -> rate.

Two model calls, both small and checkable:
  1. heading: the model sees the item and the 10 headings plain search found, and answers
     one 4-digit heading (any heading in the directory is allowed, not just the shortlist).
  2. entry: code lists the official rate entries that cover that heading (descriptions only,
     rates hidden so the model cannot steer towards a rate); the model picks one, or asks
     the shopkeeper one yes/no question when the entries differ on a condition.
Code then reads the rate from the chosen entry. The model never writes a rate.
"""
import json
import re
from dataclasses import dataclass, field

import sentry_sdk

from db import query


def _chat(llm, messages, step):
    """One model call, recorded as a Sentry gen_ai.chat span (no-op when Sentry is off)."""
    model = getattr(llm, "model_name", "unknown")
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as span:
        span.set_data("gen_ai.operation.name", "chat")
        span.set_data("gen_ai.request.model", model)
        span.set_data("gen_ai.request.messages", json.dumps(messages, ensure_ascii=False)[:4000])
        span.set_data("hsn.step", step)
        out = llm(messages)
        span.set_data("gen_ai.response.text", (out or "")[:2000])
        return out


def _tool(name, fn, *args):
    with sentry_sdk.start_span(op="gen_ai.execute_tool", name=f"execute_tool {name}") as span:
        span.set_data("gen_ai.tool.name", name)
        span.set_data("gen_ai.tool.input", json.dumps(args, ensure_ascii=False)[:500])
        out = fn(*args)
        span.set_data("gen_ai.tool.output", json.dumps(out, ensure_ascii=False, default=str)[:1500])
        return out

HEADING_SYSTEM = (
    "You classify items from small Indian shop bills into 4-digit HSN headings for GST. "
    "Item names can be brands, abbreviations, Hinglish, Hindi or Kannada. "
    'Answer JSON only: {"heading": "dddd"}. If the item is not goods or is unreadable, answer {"heading": "none"}.'
)
ENTRY_SYSTEM = (
    "You match an item from an Indian shop bill to the GST notification entry that describes it. "
    "Pick the single entry that covers this item. If the right entry depends on a fact the item name "
    "does not tell (pre-packaged and labelled or loose, sale value per piece or pair, end use), ask the "
    'shopkeeper one short yes/no question instead. Answer JSON only: {"entry": "<entry id>"} or {"ask": "<question>"}.'
)


@dataclass
class Result:
    item: str
    heading: str | None = None
    candidates: list[str] = field(default_factory=list)
    entry_id: str | None = None
    ask: str | None = None
    rate: float | None = None
    status: str = "unknown"  # certain | ask | no_entry | bad_heading | unknown
    raw: list[str] = field(default_factory=list)


def _query_text(item: str) -> str:
    q = re.sub(r"[^a-zA-Z ]+", " ", item.lower())
    q = re.sub(r"\b(pkt|pc|pcs|gm|gms|g|kg|ltr|l|ml|dz|nos|no|rs|mrp|box|pack|x)\b", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def search_headings(item: str, k: int = 10) -> list[tuple[str, str]]:
    """Plain BM25 search (pg_textsearch on Tiger Data) over code and rate-entry descriptions, folded to headings."""
    q = _query_text(item)
    if not q:
        return []
    rows = query(
            """
            with hits as (
              (select heading, description, description <@> to_bm25query(%(q)s, 'hsn_code_bm25') as s
                 from hsn_code order by s limit 60)
              union all
              (select heading, description, description <@> to_bm25query(%(q)s, 'rate_entry_bm25') as s
                 from rate_entry where heading is not null order by s limit 30)
            )
            select heading, min(s) as best, (array_agg(left(description, 140) order by s))[1]
              from hits where s < 0 group by heading order by best limit %(k)s
            """,
            {"q": q, "k": k},
        )
    return [(h, d) for h, _, d in rows]


_UPTO_2500 = re.compile(r"not exceeding\s+Rs\.?\s*2500", re.I)
_UNIT = r"(?:g|gm|gms|kg|ml|l|ltr|mm|cm|m|mtr|pc|pcs|pkt|dz|nos|no|w|v|a|x|inch|in|tier|pg|sheets?|waist|size|pair|pairs)\b"


def sale_value(item: str) -> float | None:
    """Price written in the item text ("shirt 899", "chappal Rs.199", "@3499/-"), ignoring sizes like 500ml, 4x125g or 32 waist."""
    text = re.sub(r"(?:rs\.?|₹|inr|mrp|@)\s*", " ", item, flags=re.I)
    nums = [float(m.group(1).replace(",", "")) for m in re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?!\s*" + _UNIT + r")(?![\w.])", text, re.I)]
    nums = [n for n in nums if n >= 50]
    return nums[-1] if nums else None


def heading_exists(h: str) -> bool:
    return bool(query("select 1 from hsn_code where heading = %s limit 1", (h,)))


def entries_for_heading(h: str) -> list[dict]:
    """Official entries whose HSN prefix covers this heading (chapter-, heading- or deeper-level)."""
    rows = query(
            """select distinct on (entry_id) entry_id, rate, hsn_prefix, description, has_condition, condition_note
                 from rate_entry
                where hsn_prefix <> '' and (%(h)s like hsn_prefix || '%%' or hsn_prefix like %(h)s || '%%')
                order by entry_id, length(hsn_prefix) desc""",
            {"h": h},
        )
    return [dict(entry_id=r[0], rate=float(r[1]), prefix=r[2], description=r[3], has_condition=r[4], condition=r[5]) for r in rows]


def heading_messages(item: str, cands: list[tuple[str, str]]) -> list[dict]:
    lines = "\n".join(f"{h}: {d}" for h, d in cands) or "(search found nothing)"
    return [
        {"role": "system", "content": HEADING_SYSTEM},
        {"role": "user", "content": f"Item: {item}\nPlain search suggested (may be wrong):\n{lines}"},
    ]


def entry_messages(item: str, heading: str, entries: list[dict]) -> list[dict]:
    lines = "\n".join(f"{e['entry_id']} [HSN {e['prefix']}]: {e['description'][:400]}" for e in entries)
    return [
        {"role": "system", "content": ENTRY_SYSTEM},
        {"role": "user", "content": f"Item: {item}\nHeading: {heading}\nEntries:\n{lines}"},
    ]


def _json(text: str) -> dict:
    m = re.search(r"\{.*?\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def classify(item: str, llm) -> Result:
    """llm(messages) -> text. Same function for every model we compare."""
    with sentry_sdk.start_span(op="gen_ai.invoke_agent", name="invoke_agent hsn-classifier") as span:
        span.set_data("gen_ai.agent.name", "hsn-classifier")
        span.set_data("gen_ai.request.model", getattr(llm, "model_name", "unknown"))
        r = _classify(item, llm)
        span.set_data("hsn.item", item)
        span.set_data("hsn.status", r.status)
        span.set_data("hsn.heading", r.heading)
        span.set_data("hsn.entry_id", r.entry_id)
        span.set_data("hsn.rate", r.rate)
        return r


def _classify(item: str, llm) -> Result:
    r = Result(item)
    cands = _tool("search_headings", search_headings, item)
    r.candidates = [h for h, _ in cands]
    out = _chat(llm, heading_messages(item, cands), "heading")
    r.raw.append(out)
    h = str(_json(out).get("heading", "")).strip()
    if not re.fullmatch(r"\d{4}", h) or not _tool("heading_exists", heading_exists, h):
        r.status = "bad_heading"  # guard: a heading that is not in the official directory is never shown
        return r
    r.heading = h
    entries = _tool("official_entries", entries_for_heading, h)
    if not entries:
        r.status = "no_entry"  # no schedule names it: usually the residual entry, the accountant confirms
        return r
    if len(entries) == 1 and not entries[0]["has_condition"]:
        r.entry_id, r.rate, r.status = entries[0]["entry_id"], entries[0]["rate"], "certain"
        return r
    low = next((e for e in entries if _UPTO_2500.search(e["description"])), None)
    if low:
        # Clothing, made-ups and footwear: the Rs 2,500 line is arithmetic, so code applies it, not the model.
        price = _tool("sale_value", sale_value, item)
        if price is None:
            r.ask, r.status = "Is the sale value above Rs 2,500 per piece (or per pair)?", "ask"
            return r
        if price <= 2500:
            r.entry_id, r.rate, r.status = low["entry_id"], low["rate"], "certain"
            return r
        entries = [e for e in entries if e is not low]
        if len(entries) == 1:
            r.entry_id, r.rate, r.status = entries[0]["entry_id"], entries[0]["rate"], "certain"
            return r
    out = _chat(llm, entry_messages(item, h, entries), "entry")
    r.raw.append(out)
    j = _json(out)
    by_id = {e["entry_id"]: e for e in entries}
    if j.get("entry") in by_id:
        e = by_id[j["entry"]]
        r.entry_id, r.rate, r.status = e["entry_id"], e["rate"], "certain"
    elif j.get("ask"):
        r.ask, r.status = str(j["ask"])[:200], "ask"
    else:
        r.status = "unknown"  # guard: an entry id that is not in the list is never accepted
    return r
