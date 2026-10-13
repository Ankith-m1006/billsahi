# BillSahi

The right HSN code and GST rate for a small Indian shop's items, and a check of every supplier bill for the wrong rate.

Type items the way a shop writes them (brands, Hinglish, Hindi or Kannada, abbreviations, pack sizes) or upload a photo of a supplier's GST bill. BillSahi finds the 4-digit HSN heading, matches the official GST notification entry, and reads the rate from that entry.

**The model never states a rate.** It picks a heading and an entry; code reads the rate from the official CBIC notifications. When the rate depends on something the item name doesn't say (pre-packaged or loose, sale value above Rs 2,500), BillSahi asks instead of guessing. Price thresholds are applied by code.

## How it works

1. **Bill photo -> lines**: Gemma 4 (vision) transcribes each goods line: item, printed HSN, GST rate charged, taxable value.
2. **Shortlist**: BM25 search (`pg_textsearch` on Tiger Data Postgres) over the 12,604-code HSN directory and the 1,570 official rate entries.
3. **Heading, then entry**: a Qwen3.5-4B LoRA fine-tuned on Tinker answers one heading, then picks one of the official entries that cover it (rates hidden from the model).
4. **Code decides**: the rate comes from the chosen entry; Rs 2,500 thresholds use the price in the item text; a heading or entry that does not exist is rejected.
5. Every step is traced to Sentry (agent, tool and chat spans).

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
