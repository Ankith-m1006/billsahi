"""Web app: item list -> HSN + official GST rate; bill photo -> every line checked."""
import os
import pathlib
from concurrent.futures import ThreadPoolExecutor

import sentry_sdk
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import bill
import llms
import pipeline
from db import env

try:
    sentry_sdk.init(dsn=env("SENTRY_DSN"), traces_sample_rate=1.0, send_default_pii=False,
                    environment=os.environ.get("APP_ENV", "local"))
except KeyError:
    pass

STATIC = pathlib.Path(__file__).parent / "static"
MODEL_PATH = os.environ.get("HSN_MODEL_PATH")  # tinker://... sampler path of the fine-tuned model
_llm = None


def llm():
    global _llm
    if _llm is None:
        _llm = llms.tinker_model(model_path=MODEL_PATH) if MODEL_PATH else llms.ai_studio("gemma-4-31b-it")
    return _llm


app = FastAPI(title="BillSahi")


class Items(BaseModel):
    items: list[str]


def _row(r: pipeline.Result) -> dict:
    entry = None
    if r.entry_id and r.heading:
        entry = next((e for e in pipeline.entries_for_heading(r.heading) if e["entry_id"] == r.entry_id), None)
    return {"item": r.item, "heading": r.heading, "entry_id": r.entry_id, "rate": r.rate, "status": r.status,
            "ask": r.ask, "entry_text": entry["description"][:300] if entry else None}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "model": getattr(llm(), "model_name", None)}


@app.post("/api/classify")
def classify(body: Items):
    items = [i.strip() for i in body.items if i.strip()][:60]
    if not items:
        raise HTTPException(400, "Send at least one item name.")
    with sentry_sdk.start_transaction(op="hsn.classify", name="classify item list"):
        with ThreadPoolExecutor(6) as ex:
            rows = list(ex.map(lambda i: _row(pipeline.classify(i, llm())), items))
    return {"rows": rows}


@app.post("/api/bill")
async def check_bill(photo: UploadFile = File(...)):
    data = await photo.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Photo is larger than 8 MB.")
    with sentry_sdk.start_transaction(op="hsn.bill", name="check bill photo"):
        read = bill.read_bill(data, photo.content_type or "image/jpeg")
        lines = (read.get("lines") or [])[:60]
        with ThreadPoolExecutor(6) as ex:
            checked = list(ex.map(lambda l: bill.check_line(l, llm()), lines))
    over = sum(c["rupees"] for c in checked if c["rupees"] and c["rupees"] > 0)
    return {"supplier": read.get("supplier"), "invoice_no": read.get("invoice_no"), "invoice_date": read.get("invoice_date"),
            "lines": checked, "overcharged_rupees": round(over, 2)}
