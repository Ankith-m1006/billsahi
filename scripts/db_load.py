"""Load the HSN directory and the official rate entries into Tiger Data (Postgres) with BM25 search."""
import csv, pathlib, sys
import psycopg
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = {k: v.strip().strip('"') for k, v in (l.strip().split("=", 1) for l in open(ROOT / ".env", encoding="utf-8") if "=" in l and not l.startswith("#"))}

DDL = """
create extension if not exists pg_textsearch;
create extension if not exists pg_trgm;
drop table if exists hsn_code cascade;
drop table if exists rate_entry cascade;
create table hsn_code (code text primary key, heading text not null, description text not null);
create table rate_entry (
  row_id serial primary key, entry_id text not null, schedule text, rate numeric not null,
  hsn_prefix text not null, heading text, description text not null,
  has_condition boolean, condition_note text, source text);
"""

with psycopg.connect(ENV["DATABASE_URL"]) as c:
    c.execute(DDL)
    with c.cursor().copy("copy hsn_code (code, heading, description) from stdin") as cp:
        for r in csv.DictReader(open(ROOT / "data/hsn_directory.csv", encoding="utf-8")):
            cp.write_row((r["code"], r["heading"], r["description"]))
    with c.cursor().copy("copy rate_entry (entry_id, schedule, rate, hsn_prefix, heading, description, has_condition, condition_note, source) from stdin") as cp:
        for r in csv.DictReader(open(ROOT / "data/gst_goods_rates.csv", encoding="utf-8")):
            p = r["hsn_prefix"].strip()
            cp.write_row((r["entry_id"], r["schedule"], r["total_rate_pct"], p, p[:4] if len(p) >= 4 else None,
                          r["description"], r["has_condition"] == "true", r["condition_note"], r["source_notification"]))
    # BM25 (pg_textsearch) indexes for the plain-search baseline and candidate retrieval
    c.execute("create index hsn_code_bm25 on hsn_code using bm25 (description) with (text_config='english')")
    c.execute("create index rate_entry_bm25 on rate_entry using bm25 (description) with (text_config='english')")
    c.execute("create index hsn_code_trgm on hsn_code using gin (description gin_trgm_ops)")
    print(c.execute("select (select count(*) from hsn_code), (select count(*) from rate_entry)").fetchone())
    for q in ["toilet soap bar", "rubber bands", "biscuits"]:
        rows = c.execute("select heading, left(description,70), description <@> to_bm25query(%s, 'hsn_code_bm25') as s from hsn_code order by s limit 3", (q,)).fetchall()
        print(q, rows)
