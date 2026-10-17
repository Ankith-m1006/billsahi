"""Run the shop's real bills through the full bill flow (read, check, ITC, price memory, save) for one shop code."""
import json
import mimetypes
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import main  # noqa: E402

shop = sys.argv[1]
out = pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for f in sys.argv[3:]:
    p = pathlib.Path(f)
    job = main._new_job("bill", 0)
    main._run_bill(job, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/jpeg", shop, None)
    (out / (p.stem + ".json")).write_text(json.dumps(job, indent=1, default=str), encoding="utf-8")
    r = job.get("result") or {}
    print(p.name, job["stage"], job.get("error"), len(r.get("lines", [])), "lines; itc", (r.get("itc") or {}).get("ready"),
          "risk", (r.get("itc") or {}).get("credit_at_risk"), "alerts", len(r.get("price_alerts") or []), "bill", r.get("bill_id"), flush=True)
