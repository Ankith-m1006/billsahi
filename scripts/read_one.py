"""Read one bill photo with Gemma and print the lines (diagnostics)."""
import json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import bill  # noqa: E402
t = time.time()
r = bill.read_bill(pathlib.Path(sys.argv[1]).read_bytes(), "image/jpeg", model=sys.argv[2] if len(sys.argv) > 2 else "gemma-4-31b-it")
print(round(time.time() - t), "s", json.dumps(r, ensure_ascii=False, indent=0), flush=True)
