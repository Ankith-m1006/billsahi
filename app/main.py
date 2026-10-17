"""Web app: item list -> HSN + official GST rate; bill photo -> every line checked."""
import contextvars
import os
import pathlib
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import sentry_sdk
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import bill
import ledger
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


def _map(fn, items):
    """Thread pool that keeps Sentry's span context in every worker, so each item's spans join the transaction."""
    with ThreadPoolExecutor(6) as ex:
        futures = [ex.submit(contextvars.copy_context().run, fn, i) for i in items]
        return [f.result() for f in futures]


# Background jobs, so the page can show a progress bar while Gemma reads a bill or the model checks each item.
JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _new_job(kind: str, total: int) -> dict:
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "stage": "queued", "done": 0, "total": total,
           "rows": [], "result": None, "error": None, "started": time.time()}
    with _jobs_lock:
        for k in [k for k, v in JOBS.items() if time.time() - v["started"] > 3600]:
            del JOBS[k]
        JOBS[job["id"]] = job
    return job


def _run_items(job, items):
    try:
        job["stage"] = "checking"
        with sentry_sdk.start_transaction(op="hsn.classify", name="classify item list"):
            def one(i):
                row = _row(pipeline.classify(i, llm()))
                with _jobs_lock:
                    job["rows"].append(row)
                    job["done"] += 1
                return row
            rows = _map(one, items)
        job["result"] = {"rows": rows}
        job["stage"] = "done"
    except Exception as ex:  # surfaced to the page instead of a silent spinner
        job["error"], job["stage"] = str(ex)[:300], "error"


def _run_bill(job, data, mime, shop=None, shop_gstin=None):
    try:
        with sentry_sdk.start_transaction(op="hsn.bill", name="check bill photo"):
            job["stage"] = "reading"
            read = bill.read_bill(data, mime)
            lines = (read.get("lines") or [])[:60]
            job.update(stage="checking", total=len(lines), meta={k: read.get(k) for k in ("supplier", "invoice_no", "invoice_date", "supplier_gstin", "buyer", "buyer_gstin")})

            def one(line):
                c = bill.check_line(line, llm())
                with _jobs_lock:
                    job["rows"].append(c)
                    job["done"] += 1
                return c
            checked = _map(one, lines)
        recon = ledger.reconcile(read, checked)
        if not recon["ok"]:
            # The lines don't add up to the printed total, so the photo was probably misread (rows shifted, a total
            # row taken as an item). A wrong rupee verdict is worse than none: those lines go back to "check".
            for c in checked:
                if c["verdict"] in ("overcharged", "undercharged"):
                    c["verdict"], c["rupees"] = "check", None
                    c["ask"] = "The bill's lines don't add up to its total, so this line may be misread. Check it on the paper bill."
            for c in checked:
                c["unit_cost"] = None  # misread prices must not enter the price memory
        over = sum(c["rupees"] for c in checked if c["rupees"] and c["rupees"] > 0)
        itc = ledger.itc_check(read, checked, shop_gstin)
        alerts, bill_id, saved_error = [], None, None
        if shop:
            job["stage"] = "saving"
            try:
                alerts = ledger.price_alerts(shop, checked)
                bill_id = ledger.save_bill(shop, read, checked, itc)
            except Exception as ex:  # the GST check is still useful if the shop's records can't be reached
                saved_error = str(ex)[:200]
                sentry_sdk.capture_exception(ex)
        job["result"] = {**job["meta"], "lines": checked, "overcharged_rupees": round(over, 2), "itc": itc,
                         "price_alerts": alerts, "bill_id": bill_id, "already_saved": bool(shop) and bill_id is None and not saved_error,
                         "save_error": saved_error, "amount_due": ledger.num(read.get("amount_due")),
                         "bill_total": ledger.num(read.get("bill_total")), "reconcile": recon}
        job["stage"] = "done"
    except Exception as ex:
        job["error"], job["stage"] = str(ex)[:300], "error"


@app.post("/api/jobs/classify")
def start_classify(body: Items):
    items = [i.strip() for i in body.items if i.strip()][:60]
    if not items:
        raise HTTPException(400, "Send at least one item name.")
    job = _new_job("items", len(items))
    threading.Thread(target=contextvars.copy_context().run, args=(_run_items, job, items), daemon=True).start()
    return {"id": job["id"], "total": job["total"]}


SHOP_RE = re.compile(r"^[a-z0-9-]{4,40}$")
DEMO_SHOPS = {s for s in os.environ.get("DEMO_SHOPS", "kvr-demo").split(",") if s}  # read-only books anyone can open


def _shop(shop: str | None) -> str | None:
    if shop is None or shop == "":
        return None
    if not SHOP_RE.match(shop):
        raise HTTPException(400, "Bad shop code.")
    return shop


@app.post("/api/jobs/bill")
async def start_bill(photo: UploadFile = File(...), shop: str | None = Form(None), shop_gstin: str | None = Form(None)):
    data = await photo.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Photo is larger than 8 MB.")
    job = _new_job("bill", 0)
    shop = _shop(shop)
    if shop in DEMO_SHOPS:
        shop = None  # the demo book stays as it is; the bill is still checked, just not saved
    args = (_run_bill, job, data, photo.content_type or "image/jpeg", shop, (shop_gstin or "").strip()[:20] or None)
    threading.Thread(target=contextvars.copy_context().run, args=args, daemon=True).start()
    return {"id": job["id"]}


class Payment(BaseModel):
    supplier: str
    amount: float
    note: str | None = None


@app.get("/api/shop/{shop}")
def shop_records(shop: str):
    shop = _shop(shop)
    return {"dues": ledger.dues(shop), "bills": ledger.history(shop), "demo": shop in DEMO_SHOPS}


@app.post("/api/shop/{shop}/payments")
def add_payment(shop: str, body: Payment):
    shop = _shop(shop)
    if shop in DEMO_SHOPS:
        raise HTTPException(403, "This is the demo shop, so it can't be changed. Check a bill to start your own book.")
    if not (0 < body.amount < 1e9):
        raise HTTPException(400, "Amount must be more than zero.")
    ledger.add_payment(shop, body.supplier, body.amount, (body.note or "")[:200] or None)
    return {"dues": ledger.dues(shop)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown or expired job.")
    with _jobs_lock:
        return {k: job.get(k) for k in ("id", "kind", "stage", "done", "total", "rows", "result", "error", "meta")} | {"elapsed": round(time.time() - job["started"], 1)}


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
        rows = _map(lambda i: _row(pipeline.classify(i, llm())), items)
    return {"rows": rows}


@app.post("/api/bill")
async def check_bill(photo: UploadFile = File(...)):
    data = await photo.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Photo is larger than 8 MB.")
    with sentry_sdk.start_transaction(op="hsn.bill", name="check bill photo"):
        read = bill.read_bill(data, photo.content_type or "image/jpeg")
        lines = (read.get("lines") or [])[:60]
        checked = _map(lambda l: bill.check_line(l, llm()), lines)
    over = sum(c["rupees"] for c in checked if c["rupees"] and c["rupees"] > 0)
    return {"supplier": read.get("supplier"), "invoice_no": read.get("invoice_no"), "invoice_date": read.get("invoice_date"),
            "lines": checked, "overcharged_rupees": round(over, 2)}
