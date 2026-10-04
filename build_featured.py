#!/usr/bin/env python3
"""
FOUND-LDN — choose the "Featured" event and write featured.json.
Always a real, specific event that is on the site right now. Order of preference:
  1. A scraped event happening in the next 21 days (with a real description)
  2. A featured event with specific dates that is on now or starting soon
  3. Another featured event on the site, rotating each week
"""
import argparse, json, re, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from expire_events import LINE, status, MON

DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

def parse(line):
    ev = {}
    for k in ("id", "cat", "title", "venue", "area", "date", "time", "price", "desc", "link", "image"):
        m = re.search(rf"\b{k}:(?:'((?:[^'\\]|\\.)*)'|\"([^\"]*)\"|(\d+))", line)
        if m: ev[k] = (m.group(1) or m.group(2) or m.group(3) or "").replace("\\'", "'")
    ev["featured"] = "featured:true" in line
    if ev.get("id"): ev["id"] = int(ev["id"])
    return ev

def scraped_day(dtext, today):
    m = re.match(r"^(%s) (\d{1,2}) ([A-Za-z]{3})$" % "|".join(DAYS), dtext or "", re.I)
    if not m: return None
    try: d = date(today.year, MON[m[3].lower()], int(m[2]))
    except (KeyError, ValueError): return None
    return d if d >= today - timedelta(days=200) else date(today.year + 1, d.month, d.day)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--site", type=Path, required=True); ap.add_argument("--out", type=Path, default=Path("featured.json"))
    a = ap.parse_args(); today = date.today()
    text = a.site.read_text(encoding="utf-8"); s = text.index("const EVENTS = ["); e = text.index("\n];", s)
    live = []
    for line in text[s:e].split("\n"):
        m = LINE.match(line)
        if m and not m.group(2):
            ev = parse(m.group(3))
            if ev.get("title") and ev.get("image", "").startswith("http"): live.append(ev)

    soon = []
    for ev in live:
        d = scraped_day(ev.get("date"), today)
        if d and today <= d <= today + timedelta(days=21) and not ev.get("desc", "").startswith("Listed via"):
            soon.append((d, ev))
    pick, why = None, ""
    if soon:
        pick, why = sorted(soon, key=lambda x: x[0])[0][1], "upcoming scraped event"
    if not pick:
        dated = [ev for ev in live if ev["featured"] and status(ev.get("date"), today) == "live"]
        if dated: pick, why = dated[today.isocalendar()[1] % len(dated)], "featured dated event"
    if not pick:
        feats = [ev for ev in live if ev["featured"]] or live
        if feats: pick, why = feats[today.isocalendar()[1] % len(feats)], "weekly rotation of featured events"
    if not pick:
        print("No suitable event found; leaving featured.json unchanged."); return 0
    pick = {k: v for k, v in pick.items() if k != "featured"}
    a.out.write_text(json.dumps(pick, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Featured ({why}): {pick['title']} — {pick.get('date')}")

if __name__ == "__main__":
    sys.exit(main())
