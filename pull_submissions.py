#!/usr/bin/env python3
"""
Turn approved form submissions into updates.json for update_site.py.

Source: a CSV (published Google Sheet URL, or a local file e.g. a Formspree export).
Only rows where the "Approved" column is yes/y/true/1 are used. Re-running is safe:
update_site.py skips titles already on the site.

    python3 pull_submissions.py --csv "https://docs.google.com/.../pub?output=csv"
    python3 pull_submissions.py --csv formspree_export.csv --all     # ignore Approved column
"""
import argparse, csv, io, json, re, sys, urllib.request
from pathlib import Path

ALIASES = {  # header (lowercased, non-letters stripped) -> field
    "title": "title", "eventname": "title", "event": "title", "name": "title",
    "venue": "venue", "location": "venue",
    "area": "area", "neighbourhood": "area", "neighborhood": "area",
    "category": "cat", "cat": "cat", "type": "cat",
    "date": "date", "dates": "date", "when": "date",
    "time": "time", "price": "price", "cost": "price",
    "description": "desc", "desc": "desc", "details": "desc",
    "link": "link", "url": "link", "website": "link", "eventlink": "link",
    "image": "image", "imageurl": "image", "imagelink": "image", "zone": "zone",
    "approved": "_approved",
}
def key(h): return ALIASES.get(re.sub(r"[^a-z]", "", h.lower()))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", type=Path, default=Path("updates.json"))
    ap.add_argument("--all", action="store_true", help="ignore the Approved column")
    a = ap.parse_args()
    raw = (urllib.request.urlopen(a.csv, timeout=30).read().decode("utf-8-sig")
           if a.csv.startswith("http") else Path(a.csv).read_text(encoding="utf-8-sig"))
    out, skipped = [], 0
    for row in csv.DictReader(io.StringIO(raw)):
        r = {key(k): (v or "").strip() for k, v in row.items() if k and key(k)}
        if not a.all and r.get("_approved", "").lower() not in ("yes", "y", "true", "1", "✓"):
            skipped += 1; continue
        r.pop("_approved", None)
        if not r.get("link", "").startswith(("http://", "https://")):
            r.pop("link", None)
        out.append({"action": "add", **{k: v for k, v in r.items() if v}})
    a.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out)} approved → {a.out} ({skipped} not approved)")

if __name__ == "__main__":
    sys.exit(main())
