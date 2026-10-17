"""Read a supplier bill photo with Gemma 4 (vision), then check every line against the official rate.

Gemma only transcribes what is printed. The money maths (tax charged vs tax due) is done here, in code.
"""
import base64
import http.client
import json
import re
import time
import urllib.error
import urllib.request

import sentry_sdk

import ledger
import pipeline
from db import env

READ_PROMPT = """This is a photo of a purchase bill from an Indian supplier to a shop. It may be a GST tax invoice,
or a plain cash bill that shows no GST at all.
Transcribe exactly what is printed. Do not guess values that are not printed; use null.
Return JSON only:
{"supplier": "...", "supplier_gstin": "GSTIN printed for the SELLER or null", "buyer": "billed-to name or null",
 "buyer_gstin": "GSTIN printed for the BUYER (bill to) or null", "invoice_no": "...", "invoice_date": "...",
 "bill_total": number or null, "amount_due": "running balance / amount due printed separately from this bill's total, or null",
 "lines": [{"item": "...", "hsn": "printed HSN or null", "mrp": number or null, "qty_number": number or null,
            "unit": "...", "taxable_value": number or null, "gst_rate_pct": number or null, "line_total": number or null}]}
qty_number is the number of units billed on that line (pieces, or kg). If a line shows cases AND pieces, convert
to pieces when the units-per-case is printed. taxable_value is the line value before GST. line_total is the
line's final amount as printed (after GST if GST is added). gst_rate_pct is the total GST rate printed for that
line (CGST+SGST, or IGST), e.g. 18; if the bill shows no GST at all, use null."""


def read_bill(image: bytes, mime: str, model: str = "gemma-4-31b-it") -> dict:
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as span:
        span.set_data("gen_ai.operation.name", "chat")
        span.set_data("gen_ai.request.model", model)
        span.set_data("hsn.step", "read_bill")
        body = {"contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
            {"text": READ_PROMPT}]}], "generationConfig": {"temperature": 0}}
        # Streamed, so a long read of a dense bill keeps the connection busy instead of being dropped as idle.
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={env('GEMINI_API_KEY')}"
        for attempt in range(3):  # AI Studio sometimes drops a long vision request; retry before giving up
            try:
                req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
                parts = []
                with urllib.request.urlopen(req, timeout=180) as r:
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        chunk = json.loads(line[5:])
                        for c in chunk.get("candidates", []):
                            parts += [p.get("text", "") for p in c.get("content", {}).get("parts", []) if not p.get("thought")]
                text = "".join(parts)
                break
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as ex:
                if attempt == 2 or (isinstance(ex, urllib.error.HTTPError) and ex.code < 500 and ex.code != 429):
                    raise
                time.sleep(5 * (attempt + 1))
        span.set_data("gen_ai.response.text", text[:2000])
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {"lines": []}


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def check_line(line: dict, llm) -> dict:
    r = pipeline.classify(line.get("item") or "", llm)
    basis = "item"
    printed4 = re.sub(r"\D", "", str(line.get("hsn") or ""))[:4]
    if r.heading is None and len(printed4) == 4:
        # Short forms like "PCNO 500ML" can't be placed from the name alone; the HSN printed on the bill is a hint.
        # Only used when the name failed, so a wrong printed HSN can't hide a wrong rate on a named item.
        r = pipeline.classify(f"{line.get('item')} (HSN printed on the bill: {printed4})", llm)
        r.item = line.get("item") or ""
        basis = "bill_hsn" if r.heading else "item"
    charged = _num(line.get("gst_rate_pct"))
    value = _num(line.get("taxable_value"))
    out = {"item": line.get("item"), "printed_hsn": line.get("hsn"), "charged_pct": charged, "taxable_value": value,
           "mrp": _num(line.get("mrp")), "qty_number": _num(line.get("qty_number")), "line_total": _num(line.get("line_total")),
           "size": ledger.size_of(line.get("item") or ""), "basis": basis,
           "heading": r.heading, "entry_id": r.entry_id, "official_pct": r.rate, "status": r.status, "ask": r.ask,
           "verdict": "check", "rupees": None}
    if r.status == "certain" and charged is not None:
        if abs(charged - r.rate) < 1e-6:
            out["verdict"] = "ok"
        else:
            out["verdict"] = "overcharged" if charged > r.rate else "undercharged"
            if value is not None:
                out["rupees"] = round(value * (charged - r.rate) / 100, 2)
    printed = re.sub(r"\D", "", str(line.get("hsn") or ""))
    out["hsn_matches"] = bool(printed) and r.heading is not None and printed[:4] == r.heading
    out["unit_cost"] = ledger.unit_cost(out)
    return out
