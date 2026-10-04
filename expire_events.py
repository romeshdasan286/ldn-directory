#!/usr/bin/env python3
"""
FOUND-LDN — hide events whose dates have passed, bring annual ones back in time.

Looks at the date text of every event in the EVENTS list:
  - Recurring ("Every Saturday", "Monthly – 1st Friday", "Daily") -> never touched.
  - Dated ("1–8 Jun", "Jun 7–8", "Ongoing May", "Late June", "Summer Fridays") ->
      hidden (commented out with  //~ ) once it has passed,
      shown again automatically 45 days before it next happens.
Nothing is deleted. Hidden lines stay in the file, so annual festivals return next year.

    python3 expire_events.py --site index.html [--dry-run]
"""
import argparse, re, sys
from datetime import date, timedelta
from pathlib import Path

LEAD_DAYS = 45
MON = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
RECUR = ("every", "monthly", "weekly", "daily", "quarterly", "various", "all weekend", "book ahead", "throughout the year")
WEEKDAY_DATE = re.compile(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday) \d{1,2} [a-z]{3}$", re.I)
SEASONS = {"summer": (6, 8), "spring": (3, 5), "autumn": (9, 11)}

def window(text, year):
    """(start, end) of a dated event in `year`, or None if recurring/unknown."""
    t = (text or "").lower().strip()
    if not t or WEEKDAY_DATE.match(t) or any(k in t for k in RECUR):
        return None
    months = [MON[m[:3]] for m in re.findall(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t)]
    days = [int(n) for n in re.findall(r"\b(\d{1,2})\b", t) if 1 <= int(n) <= 31]
    try:
        if months:
            m1, m2 = months[0], months[-1]
            if days:
                d1, d2 = days[0], days[-1]
                if m1 == m2: d1, d2 = min(days), max(days)
                s, e = date(year, m1, d1), date(year, m2, d2)
            else:
                s = date(year, m1, 1)
                e = date(year + (m2 == 12), m2 % 12 + 1, 1) - timedelta(days=1)
            if e < s: e = date(year + 1, e.month, e.day)
            return s, e
        for word, (a, b) in SEASONS.items():
            if word in t:
                return date(year, a, 1), date(year, b + 1, 1) - timedelta(days=1)
    except ValueError:
        return None
    return None

def status(text, today):
    """'recurring' | 'live' | 'ended' (next occurrence not close yet)."""
    if window(text, today.year) is None:
        return "recurring"
    for y in (today.year - 1, today.year, today.year + 1):
        s, e = window(text, y)
        if e >= today:
            return "live" if s - timedelta(days=LEAD_DAYS) <= today else "ended"
    return "ended"

LINE = re.compile(r"^(\s*)(//~ )?(\{ id:\d+,.*\},?)\s*$")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--site", type=Path, required=True); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(); today = date.today()
    text = a.site.read_text(encoding="utf-8")
    s = text.index("const EVENTS = ["); e = text.index("\n];", s)
    out, hid, back = [], [], []
    for line in text[s:e].split("\n"):
        m = LINE.match(line)
        if m:
            d = re.search(r"\bdate:'([^']*)'", m.group(3)); title = re.search(r"title:'((?:[^'\\]|\\.)*)'", m.group(3))
            st = status(d.group(1), today) if d else "recurring"
            name = (title.group(1) if title else "?").replace("\\'", "'")
            if st == "ended" and not m.group(2):
                line = f"{m.group(1)}//~ {m.group(3)}"; hid.append(f"{name} ({d.group(1)})")
            elif st == "live" and m.group(2):
                line = f"{m.group(1)}{m.group(3)}"; back.append(f"{name} ({d.group(1)})")
        out.append(line)
    print(f"Hidden {len(hid)}: " + "; ".join(hid)); print(f"Restored {len(back)}: " + "; ".join(back))
    if not a.dry_run and (hid or back):
        a.site.write_text(text[:s] + "\n".join(out) + text[e:], encoding="utf-8"); print("Wrote", a.site)

if __name__ == "__main__":
    sys.exit(main())
