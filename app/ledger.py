"""The shop's own records: every checked bill is kept, so BillSahi can say
1. whether a bill is ready for input tax credit (ITC) once the shop leaves composition,
2. when a supplier charges more for an item than the shop has paid before,
3. how much the shop owes each supplier.

Everything here is plain code and SQL; no model is involved.
"""
import re
import threading
import time

import db

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def gstin_ok(g: str | None) -> bool:
    """Format and check digit of a GSTIN (the 15th character is a mod-36 checksum of the first 14)."""
    g = re.sub(r"\s", "", (g or "").upper())
    if not GSTIN_RE.match(g):
        return False
    total = 0
    for i, c in enumerate(g[:14]):
        p = _CHARS.index(c) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return _CHARS[(36 - total % 36) % 36] == g[14]


def num(v):
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


# ---------- 0. did we read the bill right? ----------

def reconcile(read: dict, checked: list[dict]) -> dict:
    """The printed lines must add up to the printed bill total; if not, the photo was misread somewhere."""
    total = num(read.get("bill_total"))
    amounts = [c.get("line_total") for c in checked]
    if total is None or any(a is None for a in amounts) or not amounts:
        return {"ok": True, "checked": False, "lines_sum": None, "bill_total": total}
    s = round(sum(amounts), 2)
    # small gaps are rounding or a round-off line; anything bigger means a line was lost, doubled or shifted
    return {"ok": abs(s - total) <= max(2.0, total * 0.005), "checked": True, "lines_sum": s, "bill_total": total}


# ---------- 1. ITC readiness ----------

def itc_check(read: dict, checked: list[dict], shop_gstin: str | None) -> dict:
    """What a regular GST dealer would need on this bill to claim the tax back, and the rupees at risk if it is missing."""
    tax = 0.0
    for c in checked:
        if c.get("taxable_value") is not None and c.get("charged_pct") is not None:
            tax += c["taxable_value"] * c["charged_pct"] / 100
    sup = (read.get("supplier_gstin") or "").strip()
    buyer = (read.get("buyer_gstin") or "").strip()
    lines_taxed = [c for c in checked if c.get("charged_pct") is not None]
    no_tax = not lines_taxed
    checks = [
        {"key": "supplier_gstin", "ok": gstin_ok(sup),
         "label": "Supplier's GSTIN printed and valid",
         "detail": (sup if gstin_ok(sup) else f"{sup}: check digit does not match (misprinted, or misread from the photo)") if sup else "no GSTIN on the bill"},
        {"key": "buyer_gstin", "ok": bool(buyer) and (not shop_gstin or re.sub(r"\s", "", buyer.upper()) == re.sub(r"\s", "", shop_gstin.upper())),
         "label": "Your GSTIN printed on the bill",
         "detail": buyer or "blank: the tax will not show against your GSTIN"},
        {"key": "tax_shown", "ok": len(lines_taxed) == len(checked) and bool(checked),
         "label": "GST shown on every line",
         "detail": "no GST shown anywhere on the bill" if no_tax else f"{len(lines_taxed)} of {len(checked)} lines"},
    ]
    bad_hsn = [c for c in checked if c.get("printed_hsn") and c.get("heading") and not c.get("hsn_matches")]
    no_hsn = [c for c in checked if not c.get("printed_hsn")]
    checks.append({"key": "hsn", "ok": not bad_hsn and not no_hsn, "label": "HSN printed and right on every line",
                   "detail": "; ".join(f"{c['item']}: bill says {c['printed_hsn']}, should be {c['heading']}" for c in bad_hsn[:4])
                   or (f"{len(no_hsn)} lines without HSN" if no_hsn else "all match")})
    wrong = [c for c in checked if c.get("verdict") in ("overcharged", "undercharged")]
    checks.append({"key": "rate", "ok": not wrong, "label": "GST rate right on every line",
                   "detail": "; ".join(f"{c['item']}: {c['charged_pct']:g}% charged, {c['official_pct']:g}% due" for c in wrong[:4]) or "all match"})

    whole_bill = not (checks[0]["ok"] and checks[1]["ok"])
    if no_tax:
        at_risk, note = None, "No GST is shown, so no credit can be claimed on this purchase."
    elif whole_bill:
        at_risk, note = round(tax, 2), "Without both GSTINs on the bill, none of its GST can be claimed."
    else:
        part = sum(c["taxable_value"] * c["charged_pct"] / 100 for c in bad_hsn + wrong
                   if c.get("taxable_value") is not None and c.get("charged_pct") is not None)
        at_risk = round(part, 2)
        note = "Lines with the wrong HSN or rate can be questioned; ask the supplier for a corrected invoice." if part else "Ready for credit."
    purchase = num(read.get("bill_total")) or round(sum((c.get("line_total") or 0) for c in checked), 2) or None
    return {"ready": all(c["ok"] for c in checks), "checks": checks, "gst_on_bill": round(tax, 2),
            "credit_at_risk": at_risk, "purchase_value": purchase, "no_tax": no_tax, "note": note}


# ---------- storage ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS shop_bill (
  id serial PRIMARY KEY, shop text NOT NULL, supplier text, supplier_key text, supplier_gstin text,
  invoice_no text, invoice_date text, total numeric, printed_balance numeric, itc_ready boolean,
  credit_at_risk numeric, created_at timestamptz DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS shop_bill_once ON shop_bill (shop, supplier_key, invoice_no);
CREATE TABLE IF NOT EXISTS shop_bill_line (
  id serial PRIMARY KEY, bill_id int REFERENCES shop_bill(id) ON DELETE CASCADE, item text, item_key text,
  size text, mrp numeric, qty numeric, unit_cost numeric, gst_pct numeric);
CREATE INDEX IF NOT EXISTS shop_bill_line_bill ON shop_bill_line (bill_id);
CREATE TABLE IF NOT EXISTS shop_payment (
  id serial PRIMARY KEY, shop text NOT NULL, supplier_key text NOT NULL, amount numeric NOT NULL,
  note text, paid_on date DEFAULT current_date, created_at timestamptz DEFAULT now());
"""
_ready = False
_lock = threading.Lock()


def _ensure():
    global _ready
    with _lock:
        if not _ready:
            with db.conn() as c:
                c.execute(SCHEMA)
            _ready = True


def _run(sql, params=None, fetch=True):
    _ensure()
    if sql.lstrip().upper().startswith("SELECT"):
        return db.query(sql, params)  # reads reuse this thread's open connection
    with db.conn() as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if fetch and cur.description else None


def supplier_key(name: str | None) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in {"m", "s", "ms", "the", "and"}) or "unknown supplier"


# ---------- 2. price memory ----------

_UNIT = {"g": "g", "gm": "g", "gms": "g", "grm": "g", "gram": "g", "grams": "g", "kg": "kg", "kgs": "kg",
         "ml": "ml", "l": "l", "ltr": "l", "lt": "l", "litre": "l", "liter": "l"}
_NOISE = {"pcs", "pc", "pkt", "rs", "x", "of", "cs", "new", "offer", "set", "the", "and", "with"}


def size_of(item: str) -> str | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kgs?|gms?|grams?|grm|g|ml|ltr|litre|liter|lt|l)\b", (item or "").lower())
    return f"{float(m.group(1)):g}{_UNIT[m.group(2)]}" if m else None


def item_tokens(item: str) -> set[str]:
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(kgs?|gms?|grams?|grm|g|ml|ltr|litre|liter|lt|l)\b", " ", (item or "").lower())
    return {w for w in re.findall(r"[a-z]+|\d+", s) if w not in _NOISE and len(w) > 1}


def item_key(item: str) -> str:
    return " ".join(sorted(item_tokens(item))) + (f" {size_of(item)}" if size_of(item) else "")


def same_item(a: dict, b: dict) -> bool:
    """Same product: same pack size and MRP when both are printed, and most name words shared."""
    if a.get("size") and b.get("size") and a["size"] != b["size"]:
        return False
    if a.get("mrp") and b.get("mrp") and abs(float(a["mrp"]) - float(b["mrp"])) > 0.01:
        return False
    ta, tb = set(a["tokens"]), set(b["tokens"])
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def unit_cost(line: dict) -> float | None:
    """What one unit costs the shop, GST included (a composition dealer cannot claim the GST back)."""
    qty = num(line.get("qty_number"))
    if not qty or qty <= 0:
        return None
    total = num(line.get("line_total"))
    if total is None and line.get("taxable_value") is not None:
        total = line["taxable_value"] * (1 + (line.get("charged_pct") or 0) / 100)
    return round(total / qty, 2) if total else None


def price_alerts(shop: str, lines: list[dict], exclude_bill: int | None = None) -> list[dict]:
    """For every line, the cheapest earlier price of the same item from any supplier; flag it if this bill is dearer."""
    rows = _run("""SELECT l.item, l.size, l.mrp, l.unit_cost, b.supplier, b.invoice_date, b.id
                   FROM shop_bill_line l JOIN shop_bill b ON b.id = l.bill_id
                   WHERE b.shop = %s AND l.unit_cost IS NOT NULL AND b.id IS DISTINCT FROM %s""", (shop, exclude_bill)) or []
    past = [{"item": r[0], "size": r[1], "mrp": r[2], "unit_cost": float(r[3]), "supplier": r[4], "date": r[5],
             "tokens": item_tokens(r[0])} for r in rows]
    out = []
    for i, l in enumerate(lines):
        cost = l.get("unit_cost")
        if cost is None:
            continue
        me = {"size": l.get("size"), "mrp": l.get("mrp"), "tokens": item_tokens(l.get("item"))}
        matches = [p for p in past if same_item(me, p)]
        if not matches:
            continue
        best = min(matches, key=lambda p: p["unit_cost"])
        diff = round(cost - best["unit_cost"], 2)
        if diff >= 0.5 and diff / best["unit_cost"] >= 0.02:
            out.append({"line": i, "item": l.get("item"), "unit_cost": cost, "best_cost": best["unit_cost"],
                        "best_supplier": best["supplier"], "best_date": best["date"], "best_item": best["item"],
                        "extra_per_unit": diff, "extra_total": round(diff * (num(l.get("qty_number")) or 0), 2)})
    return out


# ---------- saving a bill and 3. dues ----------

def save_bill(shop: str, read: dict, checked: list[dict], itc: dict) -> int | None:
    if not (read.get("supplier") or "").strip():
        read = {**read, "supplier": "Supplier with no name on the bill"}
    key = supplier_key(read.get("supplier"))
    inv = (read.get("invoice_no") or "").strip() or None
    total = num(read.get("bill_total")) or itc.get("purchase_value")
    _ensure()
    for attempt in range(3):  # one transaction per bill, so a dropped connection never leaves half a bill
        try:
            with db.conn() as c:
                row = c.execute("""INSERT INTO shop_bill (shop, supplier, supplier_key, supplier_gstin, invoice_no, invoice_date,
                                                         total, printed_balance, itc_ready, credit_at_risk)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                   ON CONFLICT (shop, supplier_key, invoice_no) DO NOTHING RETURNING id""",
                                (shop, read.get("supplier"), key, read.get("supplier_gstin"), inv, read.get("invoice_date"),
                                 total, num(read.get("amount_due")), itc["ready"], itc.get("credit_at_risk"))).fetchone()
                if not row:
                    return None  # already saved: the same invoice is not counted twice
                with c.cursor() as cur:
                    cur.executemany("""INSERT INTO shop_bill_line (bill_id, item, item_key, size, mrp, qty, unit_cost, gst_pct)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    [(row[0], x.get("item"), item_key(x.get("item")), x.get("size"), x.get("mrp"),
                                      num(x.get("qty_number")), x.get("unit_cost"), x.get("charged_pct")) for x in checked])
                return row[0]
        except db.psycopg.OperationalError:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def add_payment(shop: str, supplier: str, amount: float, note: str | None):
    _run("INSERT INTO shop_payment (shop, supplier_key, amount, note) VALUES (%s,%s,%s,%s)",
         (shop, supplier_key(supplier), amount, note), fetch=False)


def dues(shop: str) -> list[dict]:
    bills = _run("""SELECT supplier_key, max(supplier), count(*), coalesce(sum(total),0), max(created_at),
                           (array_agg(printed_balance ORDER BY created_at DESC) FILTER (WHERE printed_balance IS NOT NULL))[1]
                    FROM shop_bill WHERE shop = %s GROUP BY supplier_key""", (shop,)) or []
    paid = dict(_run("SELECT supplier_key, sum(amount) FROM shop_payment WHERE shop = %s GROUP BY supplier_key", (shop,)) or [])
    known = {b[0] for b in bills}
    bills = list(bills) + [(k, k.title(), 0, 0, None, None) for k in paid if k not in known]  # paid ahead of any bill
    out = []
    for k, name, n, total, _, printed in bills:
        p = float(paid.get(k) or 0)
        out.append({"supplier": name or k, "key": k, "bills": n, "billed": float(total), "paid": p,
                    "owed": round(float(total) - p, 2), "supplier_says": float(printed) if printed is not None else None})
    return sorted(out, key=lambda d: -d["owed"])


def history(shop: str) -> list[dict]:
    rows = _run("""SELECT id, supplier, invoice_no, invoice_date, total, itc_ready, credit_at_risk,
                          (SELECT count(*) FROM shop_bill_line l WHERE l.bill_id = b.id)
                   FROM shop_bill b WHERE shop = %s ORDER BY created_at DESC LIMIT 50""", (shop,)) or []
    return [{"id": r[0], "supplier": r[1], "invoice_no": r[2], "invoice_date": r[3], "total": float(r[4] or 0),
             "itc_ready": r[5], "credit_at_risk": float(r[6]) if r[6] is not None else None, "lines": r[7]} for r in rows]
