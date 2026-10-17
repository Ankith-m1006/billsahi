# BillSahi

Is your supplier's bill *sahi*? BillSahi checks a small Indian shop's supplier bills: the GST rate on every line, whether the bill is good enough to claim the GST back, whether a supplier is charging more than the shop paid before, and how much the shop owes each supplier.

Live: https://billsahi-pb7k.onrender.com · Demo book with four real bills from a supermarket near Pavagada, Tumkur: https://billsahi-pb7k.onrender.com/?shop=kvr-demo#book

Type items the way a shop writes them (brands, Hinglish, Hindi or Kannada, abbreviations, pack sizes) or upload a photo of a supplier's GST bill. BillSahi finds the 4-digit HSN heading, matches the official GST notification entry, and reads the rate from that entry.

**The model never states a rate.** It picks a heading and an entry; code reads the rate from the official CBIC notifications. When the rate depends on something the item name doesn't say (pre-packaged or loose, sale value above Rs 2,500), BillSahi asks instead of guessing. Price thresholds are applied by code.

## What it does with a bill photo

- **GST rate on every line**, in rupees: overcharged or undercharged against the official rate.
- **Can you claim this GST back?** The supplier's GSTIN (format and check digit), your GSTIN on the bill, GST shown on every line, HSN and rate right on every line, and the rupees of credit at risk. Built for a composition shop that moves to regular GST and starts claiming input tax credit.
- **Paid less before?** Every line is saved with what one unit cost the shop. A later bill that charges more for the same item (same pack size and MRP, name matched even when suppliers spell it differently) is flagged with the rupees extra.
- **My suppliers**: billed, paid and owed per supplier, with the supplier's own printed balance next to it. Payments are recorded in one tap. Each phone gets its own private shop code.
- **It refuses to guess on a bad photo.** If the lines don't add up to the bill's printed total, the photo was misread somewhere: no line is marked overcharged and its prices are not saved.

## How it works

1. **Bill photo -> lines**: Gemma 4 31B (vision, streamed) transcribes the bill: supplier and buyer GSTIN, totals, and each goods line (item, printed HSN, MRP, quantity, GST rate, taxable value, line total). Code then checks that the lines add up to the printed total.
2. **Shortlist**: BM25 search (`pg_textsearch` on Tiger Data Postgres) over the 12,604-code HSN directory and the 1,570 official rate entries.
3. **Heading, then entry**: a Qwen3.5-4B LoRA fine-tuned on Tinker answers one heading, then picks one of the official entries that cover it (rates hidden from the model).
4. **Code decides**: the rate comes from the chosen entry; Rs 2,500 thresholds use the price in the item text; a heading or entry that does not exist is rejected.
5. **Short forms**: when the item name alone can't be placed ("PCNO 500ML" for Parachute coconut oil), the HSN printed on the bill is used as a hint. Only then, so a wrong printed HSN can't hide a wrong rate on a named item.
6. **The shop's book** (`app/ledger.py`): bills, lines and payments in Postgres on Tiger Data; the credit check, price matching and dues are plain code and SQL.
7. Every step is traced to Sentry (agent, tool and chat spans).

The live app runs the heading and entry steps on Gemma 4 31B; set `HSN_MODEL_PATH` to use the fine-tuned Qwen3.5-4B on Tinker instead.

## Real bills

Four purchase bills from one supermarket (Sep-Oct 2026), 62 lines:

| Bill | Lines | Read right | What BillSahi found |
|---|---|---|---|
| Hair-oil and soap distributor | 10 | 10 | Aloe vera skin gel billed as hair oil (HSN 3305, 5%) instead of 3304 (18%): GST short by Rs 174.57. Shop's GSTIN missing, so none of the bill's Rs 834.76 GST could be claimed. |
| Detergent distributor | 3 | 3 | Rates right. Shop's GSTIN missing: Rs 556.48 of GST not claimable. Supplier's printed balance Rs 2,13,672 against this bill's Rs 3,648. |
| Cash bill, no supplier name or GSTIN | 31 | 31 | No GST shown at all on Rs 15,252 of purchases. 30 of 31 items placed (hair colour was not). |
| FMCG distributor (dense, blurry forwarded photo) | 18 | partly | Several names and HSN digits misread, and from line 15 the rows shifted by one. The lines add up to Rs 27,060 against a printed Rs 13,745, so BillSahi marks nothing and asks for a clearer photo. |

The bill photos are private and not in this repository.

## Data

- `data/gst_goods_rates.csv`: every goods entry of Notification 9/2025-CT(Rate) and 10/2025-CT(Rate) (effective 22 Sep 2025), with amendments 19/2025 and 01/2026 applied. Sources, hashes and spot checks in `data/RATES_PROVENANCE.md`.
- `data/hsn_directory.csv`: the HSN directory from [hsn-code-package](https://www.npmjs.com/package/hsn-code-package) (ISC), codes padded to 8 digits.
- `data/synth/`: synthetic shop-style item names written by open models (Gemma 4, Qwen3.5-397B on Tinker), each kept only if a blind pass by the other model agrees on the chapter.
- `data/test/`: frozen test sets (never used for training).

## Run

```bash
pip install -r requirements.txt
# .env: DATABASE_URL, GEMINI_API_KEY, TINKER_API_KEY, SENTRY_DSN, HSN_MODEL_PATH (optional tinker:// sampler path)
python scripts/db_load.py
cd app && uvicorn main:app --port 8077
```

BillSahi is a helper, not tax advice. Confirm anything marked "ask" with your accountant.
