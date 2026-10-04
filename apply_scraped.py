#!/usr/bin/env python3
"""
FOUND-LDN — turn scraped events (events.json) into site updates (updates.json).

Safety gates (an event is only added if ALL pass):
  - the page had a real event date (JSON-LD), and that date is today or later
  - it has an http(s) link and image, and a title of 8+ characters
  - its link and title are not already on the site
Also removes older scraped one-off events whose date has passed.
Only entries dated like "Saturday 12 Oct" are ever removed. Your curated events are never touched.

    python3 apply_scraped.py --site index.html --events events.json --out updates.json
"""
import argparse, json, re, sys
from datetime import date, datetime, timedelta
from pathlib import Path

# scraper category -> site category. Edit to taste.
CAT_MAP = {"music": "music", "art": "art", "theatre": "theatre", "markets": "markets", "wellness": "wellness",
           "fitness": "fitness", "food": "markets", "outdoors": "eco", "kids": "create", "talks": "create",
           "film": "art", "community": "festivals"}
BOROUGH_SOURCES = {"Hackney": "Hackney", "Camden": "Camden", "Islington": "Islington"}
DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
DATE_RE = re.compile(r"^(%s) (\d{1,2}) (%s)$" % ("|".join(DAYS), "|".join(MONTHS)))

def clean_title(t):
    t = re.sub(r"\s*[|\u2013\u2014-]\s*(Time Out|Eventbrite|Secret London|Resident Advisor|RA)[^|]*$", "", t.strip(), flags=re.I)
    return re.sub(r"\s+", " ", t).strip()

def nice_time(hhmm):
    m = re.match(r"(\d{2}):(\d{2})", hhmm or "")
    if not m or hhmm.startswith("00:00"): return "Various"
    h, mi = int(m[1]), int(m[2]); ap = "am" if h < 12 else "pm"; h12 = h % 12 or 12
    return f"{h12}{ap}" if mi == 0 else f"{h12}:{mi:02d}{ap}"

def site_info(html):
    links = {l.rstrip("/") for l in re.findall(r"link:'([^']+)'", html)}
    titles = {t.replace("\\'", "'").lower() for t in re.findall(r"title:'((?:[^'\\]|\\.)*)'", html)}
    areas = {a.replace("\\'", "'") for a in re.findall(r"area:'((?:[^'\\]|\\.)*)'", html)}
    dated = re.findall(r"title:'((?:[^'\\]|\\.)*)'[^}]*?date:'([^']*)'", html)
    return links, titles, areas, dated

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", type=Path, required=True); ap.add_argument("--events", type=Path, default=Path("events.json"))
    ap.add_argument("--out", type=Path, default=Path("updates.json")); ap.add_argument("--max-new", type=int, default=15)
    a = ap.parse_args()
    today = date.today(); html = a.site.read_text(encoding="utf-8")
    links, titles, areas, dated = site_info(html)
    try: events = json.loads(a.events.read_text(encoding="utf-8"))
    except Exception: events = []
    ups, why = [], {}
    def skip(r): why[r] = why.get(r, 0) + 1

    for ev in events:
        if not isinstance(ev, dict): continue
        title = clean_title(ev.get("title", "")); link = (ev.get("link") or "").rstrip("/")
        if len(title) < 8 or not link.startswith("http"): skip("no title/link"); continue
        if ev.get("date_extraction_method") != "json-ld": skip("no real event date"); continue
        try: d = datetime.strptime(ev["date"][:10], "%Y-%m-%d").date()
        except Exception: skip("bad date"); continue
        if d < today: skip("already past"); continue
        if not str(ev.get("image", "")).startswith("http"): skip("no image"); continue
        if link in links or title.lower() in titles: skip("already on site"); continue
        src = ev.get("source", "")
        area = next((v for k, v in BOROUGH_SOURCES.items() if k in src), "Central London")
        if area not in areas: area = "Central London"
        ups.append({"action": "add", "title": title, "venue": f"Via {src}" if src else "See event page", "area": area,
                    "cat": CAT_MAP.get(ev.get("category") or ev.get("cat"), "festivals"),
                    "date": f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month-1]}", "time": nice_time(ev.get("time", "")),
                    "price": ev.get("price") or "See site",
                    "desc": ev.get("desc") or f"Listed via {src}. Check the event page for details.",
                    "link": ev["link"], "image": ev["image"],
                    "recurrence": "weekly" if (d - today).days <= 7 else "monthly"})
        links.add(link); titles.add(title.lower())
        if len(ups) >= a.max_new: break

    removed = 0
    for title, dtext in dated:                       # expire past scraped one-offs
        m = DATE_RE.match(dtext)
        if not m: continue
        try: d = date(today.year, MONTHS.index(m[3]) + 1, int(m[2]))
        except ValueError: continue
        if d < today - timedelta(days=1) and (today - d).days < 300:
            ups.append({"action": "remove", "title": title.replace("\\'", "'")}); removed += 1

    a.out.write_text(json.dumps(ups, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(events)} scraped → {len(ups)-removed} to add, {removed} expired to remove.")
    for r, n in sorted(why.items(), key=lambda x: -x[1]): print(f"  skipped {n}: {r}")

if __name__ == "__main__":
    sys.exit(main())
