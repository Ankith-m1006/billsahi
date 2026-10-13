"""Tiny Gemma 4 client (AI Studio). Key from hsn/.env, never printed."""
import json, pathlib, time, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = dict(l.strip().split("=", 1) for l in open(ROOT / ".env", encoding="utf-8") if "=" in l and not l.startswith("#"))
KEY = ENV["GEMINI_API_KEY"].strip().strip('"')

def generate(prompt, model="gemma-4-26b-a4b-it", temperature=0.9, retries=4):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={KEY}"
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": temperature}}).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            parts = d["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and i < retries - 1:
                time.sleep(15 * (i + 1)); continue
            raise
