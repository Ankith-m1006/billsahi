"""Extract text (and tables) from notification PDFs.

Usage: python -I extract_text.py <pdf_dir> <out_dir>
Writes <name>.txt (page text with ==== PAGE n ==== markers) and <name>.tables.json.
"""
import json
import sys
from pathlib import Path

import pdfplumber


def main(pdf_dir: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for pdf_path in sorted(Path(pdf_dir).glob("*.pdf")):
        texts, tables = [], []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                texts.append(f"==== PAGE {i} ====\n{page.extract_text() or ''}")
                for t in page.extract_tables():
                    tables.append({"page": i, "rows": t})
        (out / (pdf_path.stem + ".txt")).write_text("\n".join(texts), encoding="utf-8")
        (out / (pdf_path.stem + ".tables.json")).write_text(
            json.dumps(tables, ensure_ascii=False, indent=1), encoding="utf-8")
        print(pdf_path.name, len(texts), "pages", len(tables), "tables")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
