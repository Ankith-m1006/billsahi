"""Download GST rate notification PDFs from the official CBIC tax information portal.

The portal's download API returns JSON {"data": "<base64 PDF>"}; we decode and save
verbatim. Usage: python -I download_notifications.py <out_dir>
"""
import base64
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

API = "https://taxinformation.cbic.gov.in/api/cbic-notification-msts/download/{id}/{lang}"

# portal record id -> local filename
DOCS = {
    1010436: "09-2025-CTR.pdf",
    1010467: "09-2025-CTR-corrigendum.pdf",
    1010437: "10-2025-CTR.pdf",
    1010472: ("10-2025-CTR-corrigendum-HINDI-only.pdf", "HIN"),  # English copy not on portal
    1010438: "11-2025-CTR.pdf",
    1010441: "14-2025-CTR.pdf",
    1010534: "19-2025-CTR.pdf",
    1010645: "01-2026-CTR.pdf",
    1010449: "02-2025-CompCess-Rate.pdf",
    1010537: "03-2025-CompCess-Rate.pdf",
}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for rid, spec in DOCS.items():
        name, lang = spec if isinstance(spec, tuple) else (spec, "ENG")
        url = API.format(id=rid, lang=lang)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # record and continue
            print(rid, name, "FAILED", e)
            manifest.append({"record_id": rid, "file": name, "url": url, "error": str(e)})
            continue
        pdf = base64.b64decode(payload["data"])
        if not pdf.startswith(b"%PDF"):
            raise SystemExit(f"{rid}: not a PDF")
        (out / name).write_bytes(pdf)
        sha = hashlib.sha256(pdf).hexdigest()
        manifest.append({"record_id": rid, "file": name, "url": url, "bytes": len(pdf), "sha256": sha})
        print(rid, name, len(pdf), sha[:16])
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
