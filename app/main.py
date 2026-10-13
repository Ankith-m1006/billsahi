"""Web app: item list -> HSN + official GST rate; bill photo -> every line checked."""
import contextvars
import os
import pathlib
import threading
import time
import uuid
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


def _run_bill(job, data, mime):
    try:
        with sentry_sdk.start_transaction(op="hsn.bill", name="check bill photo"):
            job["stage"] = "reading"
            read = bill.read_bill(data, mime)
            lines = (read.get("lines") or [])[:60]
            job.update(stage="checking", total=len(lines), meta={k: read.get(k) for k in ("supplier", "invoice_no", "invoice_date")})

            def one(line):
                c = bill.check_line(line, llm())
                with _jobs_lock:
                    job["rows"].append(c)
                    job["done"] += 1
                return c
            checked = _map(one, lines)
        over = sum(c["rupees"] for c in checked if c["rupees"] and c["rupees"] > 0)
        job["result"] = {**job["meta"], "lines": checked, "overcharged_rupees": round(over, 2)}
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


@app.post("/api/jobs/bill")
async def start_bill(photo: UploadFile = File(...)):
    data = await photo.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Photo is larger than 8 MB.")
    job = _new_job("bill", 0)
    threading.Thread(target=contextvars.copy_context().run, args=(_run_bill, job, data, photo.content_type or "image/jpeg"), daemon=True).start()
    return {"id": job["id"]}


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
