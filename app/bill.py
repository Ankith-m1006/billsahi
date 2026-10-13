"""Read a supplier bill photo with Gemma 4 (vision), then check every line against the official rate.

Gemma only transcribes what is printed. The money maths (tax charged vs tax due) is done here, in code.
"""
import base64
import json
import re
import urllib.request

import sentry_sdk

import pipeline
from db import env

READ_PROMPT = """This is a photo of a GST purchase bill (tax invoice) from an Indian supplier.
Transcribe every goods line exactly as printed. Do not guess values that are not printed; use null.
Return JSON only:
{"supplier": "...", "invoice_no": "...", "invoice_date": "...",
 "lines": [{"item": "...", "hsn": "printed HSN or null", "qty": "...", "taxable_value": number or null,
            "gst_rate_pct": number or null}]}
gst_rate_pct is the total GST rate printed for that line (CGST+SGST, or IGST), e.g. 18."""


def read_bill(image: bytes, mime: str, model: str = "gemma-4-31b-it") -> dict:
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as span:
        span.set_data("gen_ai.operation.name", "chat")
        span.set_data("gen_ai.request.model", model)
        span.set_data("hsn.step", "read_bill")
        body = {"contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
            {"text": READ_PROMPT}]}], "generationConfig": {"temperature": 0}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={env('GEMINI_API_KEY')}"
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=240) as r:
            d = json.load(r)
        text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
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
    charged = _num(line.get("gst_rate_pct"))
    value = _num(line.get("taxable_value"))
    out = {"item": line.get("item"), "printed_hsn": line.get("hsn"), "charged_pct": charged, "taxable_value": value,
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
    return out
