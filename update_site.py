#!/usr/bin/env python3
"""
FOUND-LDN — apply event updates to the website
==============================================
Merges changes into the `const EVENTS = [...]` block of the site HTML without
touching the other (hand-curated) entries. A timestamped backup is written first.

Usage:
    python3 update_site.py --updates updates.json                 # apply to "v9 index.html"
    python3 update_site.py --updates updates.json --dry-run       # preview only
    python3 update_site.py --site index.html --updates updates.json
    python3 update_site.py --from-scraper events.json             # convert scraper output -> updates_review.json

updates.json is a list. Each item has an "action" (default "add"):
    {"action":"add",    "title":"…", "venue":"…", "area":"Peckham", "cat":"art",
                        "date":"28–29 Jun", "time":"11am – 6pm", "price":"Free",
                        "desc":"…", "link":"https://…", "image":"https://…"}
    {"action":"update", "id":15, "date":"5–6 Jul"}          # or "title":"…" instead of id
    {"action":"remove", "id":15}                            # or "title":"…"

Auto-filled on add: id, zone (looked up from areas already on the site), sortHour
(from time), free (from price), recurrence (from date), featured (false).
Override any of them by including the field.
"""
from __future__ import annotations
import argparse, json, re, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

FIELD_ORDER = ["id", "cat", "title", "venue", "area", "zone", "date", "time", "sortHour",
               "price", "free", "recurrence", "featured", "desc", "link", "image"]
REQUIRED_ADD = ["title", "venue", "area", "cat", "date", "link"]
VALUE = r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|true|false|-?[\d.]+"""


# ---------- JS helpers ----------
def js_str(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"

def js_val(k: str, v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)) and k in ("id", "sortHour"):
        return str(v)
    return js_str(v)

def render(ev: dict) -> str:
    parts = [f"{k}:{js_val(k, ev[k])}" for k in FIELD_ORDER if k in ev]
    return "  { " + ", ".join(parts) + " },"


# ---------- derive fields ----------
def derive_sort_hour(time_s: str) -> float:
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_s or "", re.I)
    if not m:
        return 12
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    v = round(h + int(m.group(2) or 0) / 60, 2)
    return int(v) if v == int(v) else v

def derive_free(price: str) -> bool:
    p = (price or "").strip().lower()
    return p.startswith("free") and "£" not in p

def derive_recurrence(date_s: str) -> str:
    d = (date_s or "").lower()
    if "mon–sat" in d or "daily" in d or "every day" in d:
        return "daily"
    if "every" in d or "weekly" in d:
        return "weekly"
    return "monthly"


# ---------- parse site ----------
def find_block(text: str) -> tuple[int, int]:
    start = text.index("const EVENTS = [")
    end = text.index("\n];", start)
    return start, end  # end points at the "\n" before "];"

def event_lines(block: str) -> dict[int, str]:
    out = {}
    for line in block.splitlines():
        m = re.match(r"\s*\{\s*id:(\d+)\s*,", line)
        if m:
            out[int(m.group(1))] = line
    return out

def field(line: str, key: str) -> str | None:
    m = re.search(rf"\b{key}:({VALUE})", line)
    if not m:
        return None
    v = m.group(1)
    return v[1:-1] if v[0] in "'\"" else v

def site_knowledge(lines: dict[int, str]):
    zones, cats = {}, set()
    for ln in lines.values():
        a, z, c = field(ln, "area"), field(ln, "zone"), field(ln, "cat")
        if a and z:
            zones[a.replace("\\'", "'").lower()] = z
        if c:
            cats.add(c)
    return zones, cats


# ---------- apply ----------
def apply(site: Path, updates: list[dict], dry: bool) -> int:
    text = site.read_text(encoding="utf-8")
    s, e = find_block(text)
    block = text[s:e]
    lines = event_lines(block)
    zones, cats = site_knowledge(lines)
    next_id = max(lines) + 1
    by_title = {(field(l, "title") or "").replace("\\'", "'").lower(): i for i, l in lines.items()}

    new_block = block
    added = updated = removed = 0
    problems: list[str] = []

    def locate(u):
        if "id" in u:
            return int(u["id"]) if int(u["id"]) in lines else None
        return by_title.get(str(u.get("title", "")).lower())

    for n, u in enumerate(updates, 1):
        act = u.get("action", "add")
        tag = f"#{n} ({u.get('title') or u.get('id')})"

        if act == "add":
            missing = [k for k in REQUIRED_ADD if not u.get(k)]
            if missing:
                problems.append(f"{tag}: missing {', '.join(missing)} — skipped"); continue
            if u["title"].lower() in by_title:
                problems.append(f"{tag}: title already on site — skipped (use action 'update')"); continue
            if u["cat"] not in cats:
                problems.append(f"{tag}: unknown cat '{u['cat']}' (site uses: {', '.join(sorted(cats))}) — skipped"); continue
            zone = u.get("zone") or zones.get(u["area"].lower())
            if not zone:
                problems.append(f"{tag}: can't work out zone for area '{u['area']}' — add a 'zone' (north/south/east/west/central) — skipped"); continue
            ev = {k: v for k, v in u.items() if k in FIELD_ORDER}
            ev.update(id=next_id, zone=zone)
            ev.setdefault("time", "Various")
            ev.setdefault("price", "See site")
            ev.setdefault("sortHour", derive_sort_hour(ev["time"]))
            ev.setdefault("free", derive_free(ev["price"]))
            ev.setdefault("recurrence", derive_recurrence(ev["date"]))
            ev.setdefault("featured", False)
            ev.setdefault("desc", "")
            ev.setdefault("image", "")
            new_line = render(ev)
            if new_block.rstrip().endswith("}"):      # last existing entry has no trailing comma
                new_block = new_block.rstrip() + ","
            if not ev["image"]:
                problems.append(f"{tag}: added without an image — cards may look empty")
            new_block += "\n" + new_line
            lines[next_id] = new_line
            by_title[u["title"].lower()] = next_id
            next_id += 1; added += 1

        elif act in ("update", "remove"):
            i = locate(u)
            if i is None:
                problems.append(f"{tag}: no matching event — skipped"); continue
            old = lines[i]
            if act == "remove":
                new_block = new_block.replace(old + "\n", "", 1) if (old + "\n") in new_block else new_block.replace("\n" + old, "", 1)
                del lines[i]; removed += 1
                continue
            new = old
            for k, v in u.items():
                if k in ("action", "id") or k not in FIELD_ORDER:
                    continue
                if k == "cat" and v not in cats:
                    problems.append(f"{tag}: unknown cat '{v}' — field skipped"); continue
                pat = re.compile(rf"(\b{k}:)({VALUE})")
                if not pat.search(new):
                    problems.append(f"{tag}: field '{k}' not on that entry — skipped"); continue
                new = pat.sub(lambda m: m.group(1) + js_val(k, v), new, count=1)
                # keep derived fields in step unless given explicitly
                if k == "time" and "sortHour" not in u:
                    new = re.sub(r"(\bsortHour:)[\d.]+", lambda m: m.group(1) + str(derive_sort_hour(v)), new, count=1)
                if k == "price" and "free" not in u:
                    new = re.sub(r"(\bfree:)(true|false)", lambda m: m.group(1) + ("true" if derive_free(v) else "false"), new, count=1)
            new_block = new_block.replace(old, new, 1)
            lines[i] = new; updated += 1
        else:
            problems.append(f"{tag}: unknown action '{act}' — skipped")

    print(f"Added {added}, updated {updated}, removed {removed}. Site now has {len(lines)} events.")
    for p in problems:
        print("  ! " + p)
    if dry:
        print("Dry run — nothing written.")
        return 0 if not problems else 1
    if not (added or updated or removed):
        print("Nothing to write."); return 1

    out = text[:s] + new_block + text[e:]
    backup = site.with_name(f"{site.stem}.{datetime.now():%Y%m%d-%H%M%S}.bak{site.suffix}")
    shutil.copy2(site, backup)
    site.write_text(out, encoding="utf-8")
    print(f"Backup: {backup.name}\nWrote:  {site.name}")
    syntax_check(site)
    return 0 if not problems else 1


def syntax_check(site: Path) -> None:
    """Best-effort: run the page's main <script> through node --check if node exists."""
    if not shutil.which("node"):
        print("(node not found — skipped JS syntax check; open the page and check the console)"); return
    html = site.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    js = max(scripts, key=len)
    tmp = site.with_name(".syntax_check.js"); tmp.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    print("JS syntax check: OK" if r.returncode == 0 else "JS syntax check FAILED — restore the .bak!\n" + r.stderr[:600])


# ---------- scraper bridge ----------
# scraper categories -> site categories; anything else is left for you to choose
CAT_MAP = {"music": "music", "art": "art", "theatre": "theatre", "markets": "markets",
           "wellness": "wellness", "fitness": "fitness"}


def from_scraper(src: Path, out: Path) -> None:
    items = json.loads(src.read_text(encoding="utf-8"))
    cands = []
    for ev in items:
        if not isinstance(ev, dict) or not ev.get("title") or not ev.get("link"):
            continue
        cands.append({
            "action": "add", "title": ev["title"], "venue": ev.get("venue", ""),
            "area": "FILL IN" if ev.get("area") in (None, "", "London") else ev["area"],
            "cat": CAT_MAP.get(ev.get("category") or ev.get("cat"), "FILL IN"),
            "date": "FILL IN" if ev.get("date") in (None, "", "Ongoing") else ev["date"],
            "time": ev.get("time") or "Various", "price": ev.get("price", "See site"),
            "desc": ev.get("desc", ""), "link": ev["link"], "image": ev.get("image", ""),
        })
    out.write_text(json.dumps(cands, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(cands)} candidates → {out}. Fill in the 'FILL IN' fields, delete what you don't want, then apply with --updates.")


def main() -> int:
    here = Path(__file__).parent
    ap = argparse.ArgumentParser(description="Apply event updates to the FOUND-LDN site")
    ap.add_argument("--site", type=Path, default=here / "v9 index.html")
    ap.add_argument("--updates", type=Path)
    ap.add_argument("--from-scraper", type=Path, help="events.json from fetch_events_v2.py")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.from_scraper:
        from_scraper(a.from_scraper, here / "updates_review.json"); return 0
    if not a.updates:
        ap.error("give --updates updates.json (or --from-scraper events.json)")
    ups = json.loads(a.updates.read_text(encoding="utf-8"))
    if any("FILL IN" in json.dumps(u) for u in ups):
        print("updates file still has 'FILL IN' placeholders — fix those first."); return 1
    return apply(a.site, ups, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
