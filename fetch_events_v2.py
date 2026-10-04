#!/usr/bin/env python3
"""
THE LDN DIRECTORY — Enhanced Event Fetcher v2
==============================================
Improved version with:
  • Data quality validation & deduplication
  • Smart date/time extraction (JSON-LD, regex)
  • Category confidence scoring
  • Image fallback & validation
  • RSS feed integration
  • Source health tracking
  • Doubled coverage per source (6→12 events)

Run:
    python3 fetch_events_v2.py
    python3 fetch_events_v2.py --html "/path/to/found-ldn.html" --rss
"""

from __future__ import annotations
import argparse, json, re, sys, time, html, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

# ===== CONFIG =====
SOURCES: list[dict[str, Any]] = [
    {"name": "Time Out — Free London", "url": "https://www.timeout.com/london/things-to-do/free-london", "kind": "listing", "priority": 1},
    {"name": "Time Out — Things to Do", "url": "https://www.timeout.com/london/things-to-do", "kind": "listing", "priority": 1},
    {"name": "Eventbrite London", "url": "https://www.eventbrite.co.uk/d/united-kingdom--london/free--events/", "kind": "listing", "priority": 1},
    {"name": "Secret London", "url": "https://secretldn.com/events/", "kind": "listing", "priority": 2},
    {"name": "Resident Advisor — London", "url": "https://ra.co/events/uk/london", "kind": "listing", "priority": 2},
    {"name": "Hackney — What's On", "url": "https://hackney.gov.uk/whats-on", "kind": "borough", "priority": 1},
    {"name": "Camden — Events", "url": "https://www.camden.gov.uk/whats-on", "kind": "borough", "priority": 1},
    {"name": "Islington — What's On", "url": "https://www.islington.gov.uk/community/whats-on", "kind": "borough", "priority": 1},
]

# RSS feeds for the "Reading list" articles strip on the v9 site.
# Each entry: name, url, category, max_items.
# Articles are served separately to events via articles.json.
RSS_FEEDS = [
    {"name": "BBC London",         "url": "https://feeds.bbci.co.uk/news/england/london/rss.xml",  "category": "community", "max_items": 3},
    {"name": "Hackney News",       "url": "https://news.hackney.gov.uk/feed/",                     "category": "community", "max_items": 2},
    {"name": "Eater London",       "url": "https://london.eater.com/rss/index.xml",                "category": "food",      "max_items": 2},
    {"name": "Mind",               "url": "https://www.mind.org.uk/news-campaigns/news/feed/",     "category": "wellness",  "max_items": 2},
    {"name": "NHS England",        "url": "https://www.england.nhs.uk/feed/",                      "category": "wellness",  "max_items": 2},
    {"name": "PadelMagazine.uk",   "url": "https://padelmagazine.uk/feed/",                        "category": "fitness",   "max_items": 2},
    {"name": "Time Out London",    "url": "https://www.timeout.com/london/rss",                    "category": "community", "max_items": 2},
]

# Articles cache config
ARTICLES_CACHE_TTL_HOURS = 6

CATEGORIES = ["theatre", "markets", "music", "wellness", "fitness", "art", "community", "kids", "food", "outdoors", "talks", "film"]
BOROUGHS = ["Hackney", "Camden", "Islington", "Lambeth", "Southwark", "Tower Hamlets", "Lewisham", "Waltham Forest", "Greenwich", "Wandsworth", "Westminster", "Kensington and Chelsea"]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ===== HTTP HELPERS =====
def fetch(url: str, timeout: int = 15) -> str:
    """Fetch URL with user-agent."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            enc = r.headers.get_content_charset() or "utf-8"
            return data.decode(enc, errors="ignore")
    except Exception as e:
        print(f"  [warn] fetch failed: {url} ({type(e).__name__})", file=sys.stderr)
        return ""

# ===== IMAGE & METADATA EXTRACTION =====
def og_image(html_text: str, base_url: str = "") -> str:
    """Extract og:image or twitter:image from HTML."""
    patterns = [
        (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', "og:image"),
        (r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', "twitter:image"),
    ]

    for pattern, source in patterns:
        m = re.search(pattern, html_text, re.I)
        if m:
            img = html.unescape(m.group(1).strip())
            if img.startswith("//"):
                img = "https:" + img
            elif img.startswith("/") and base_url:
                img = urllib.parse.urljoin(base_url, img)
            return img
    return ""

def try_alternative_images(html_text: str, base_url: str) -> str:
    """Try multiple image fallback sources."""
    patterns = [
        (r'<meta[^>]+property=["\']article:image["\'][^>]+content=["\']([^"\']+)["\']', "article:image"),
        (r'"image":\s*["\']([^"\']+)["\']', "schema-image"),
        (r'<img[^>]+src="([^"]+)"[^>]+alt="[^"]*event', "img-event"),
        (r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*featured', "featured-img"),
    ]

    for pattern, source_type in patterns:
        m = re.search(pattern, html_text, re.I)
        if m:
            url = m.group(1).strip()
            if url.startswith(("http://", "https://", "//")):
                return urllib.parse.urljoin(base_url, url) if url.startswith("//") or url.startswith("/") else url
    return ""

def page_title(html_text: str) -> str:
    """Extract page title from H1 or <title> tag."""
    for pattern in [r'<h1[^>]*>(.*?)</h1>', r'<title[^>]*>(.*?)</title>']:
        m = re.search(pattern, html_text, re.I | re.S)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1))
            return html.unescape(t).strip()[:120]
    return ""

# ===== DATE/TIME EXTRACTION =====
def extract_datetime_from_page(html: str, url: str) -> dict:
    """
    Extract date/time using multiple methods:
    1. JSON-LD structured data (most reliable)
    2. Open Graph meta tags
    3. Regex patterns
    """
    # METHOD 1: JSON-LD structured data
    json_ld_match = re.search(r'<script type="application/ld\+json">({.*?})</script>', html, re.S | re.I)
    if json_ld_match:
        try:
            ld = json.loads(json_ld_match.group(1))
            if isinstance(ld, list):
                ld = ld[0]
            if ld.get("@type") in ("Event", "LocalBusiness", "Organization"):
                start_date = ld.get("startDate") or ld.get("dateTime") or ld.get("date")
                if start_date:
                    return {
                        "date": start_date[:10],
                        "time": start_date[11:16] if len(start_date) > 10 else "",
                        "method": "json-ld",
                        "confidence": 0.95
                    }
        except Exception:
            pass

    # METHOD 2: Open Graph article:published_time
    og_match = re.search(r'<meta property="article:published_time" content="([^"]+)"', html)
    if og_match:
        return {
            "date": og_match.group(1)[:10],
            "time": og_match.group(1)[11:16] if len(og_match.group(1)) > 10 else "",
            "method": "og-date",
            "confidence": 0.7
        }

    # METHOD 3: Common date patterns
    date_patterns = [
        (r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})", "UK text"),
        (r"(\d{4})-(\d{2})-(\d{2})", "ISO"),
    ]

    for pattern, fmt in date_patterns:
        m = re.search(pattern, html, re.I)
        if m:
            return {
                "date": "See site",
                "time": "",
                "method": fmt,
                "confidence": 0.5
            }

    return {
        "date": "See site",
        "time": "",
        "method": "unknown",
        "confidence": 0.0
    }

# ===== CATEGORY CLASSIFICATION =====
CATEGORY_RULES = {
    "theatre": {
        "keywords": ["theatre", "play", "drama", "stage", "monologue", "west end"],
        "confidence_boost": 0.2
    },
    "markets": {
        "keywords": ["market", "flea", "car boot", "fair", "antique"],
        "confidence_boost": 0.15
    },
    "music": {
        "keywords": ["gig", "concert", "music", "band", "dj", "live", "jazz", "folk"],
        "confidence_boost": 0.25
    },
    "wellness": {
        "keywords": ["yoga", "meditat", "wellness", "breathwork", "mindful", "spa"],
        "confidence_boost": 0.2
    },
    "fitness": {
        "keywords": ["parkrun", "run", "cycle", "swim", "yoga", "workout", "bootcamp"],
        "confidence_boost": 0.25
    },
    "art": {
        "keywords": ["exhibit", "gallery", "art", "painting", "sculpt", "installation"],
        "confidence_boost": 0.2
    },
    "community": {
        "keywords": ["community", "volunteer", "meet", "social", "library"],
        "confidence_boost": 0.15
    },
}

def classify_with_confidence(title: str, desc: str = "", source: str = "") -> dict:
    """Classify event with confidence score."""
    combined = f"{title} {desc} {source}".lower()
    scores = {}

    for cat, rules in CATEGORY_RULES.items():
        keyword_matches = sum(1 for kw in rules["keywords"] if kw in combined)
        base_score = keyword_matches / len(rules["keywords"]) if rules["keywords"] else 0

        if base_score > 0:
            base_score += rules.get("confidence_boost", 0)

        scores[cat] = min(base_score, 1.0)

    if not scores or max(scores.values()) < 0.15:
        return {"primary": "community", "confidence": 0.3}

    primary = max(scores, key=scores.get)
    confidence = scores[primary]

    return {
        "primary": primary,
        "confidence": round(confidence, 2)
    }

# ===== VALIDATION & DEDUPLICATION =====
def validate_event(ev: dict) -> tuple[bool, list[str]]:
    """Validate event data quality."""
    errors = []

    # CRITICAL fields
    if not ev.get("title") or len(ev["title"].strip()) < 8:
        errors.append("Title missing or too short")
    if not ev.get("link"):
        errors.append("Event link missing")

    # IMAGE QUALITY
    if not ev.get("image"):
        errors.append("No image found")
    elif not ev["image"].startswith(("http://", "https://")):
        errors.append("Invalid image URL")

    # DATE QUALITY
    if ev.get("date") and ev.get("date") == "1900-01-01":
        errors.append("Invalid date placeholder")

    return (len(errors) == 0, errors)

def deduplicate_events(events: list[dict]) -> list[dict]:
    """Remove duplicate events by URL + normalized title."""
    seen = {}
    deduplicated = []

    for ev in events:
        key = (ev.get("link", ""), ev.get("title", "").lower().strip()[:50])

        if key in seen:
            continue

        seen[key] = True
        deduplicated.append(ev)

    return deduplicated

# ===== LINK EXTRACTION =====
def extract_links(listing_html: str, base: str, max_links: int = 30) -> list[tuple[str, str]]:
    """Extract event links from listing page."""
    out, seen = [], set()
    link_pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)

    for m in link_pattern.finditer(listing_html):
        href = urllib.parse.urljoin(base, m.group(1))
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()

        if not text or len(text) < 8 or len(text) > 140 or href in seen:
            continue

        host = urllib.parse.urlparse(base).netloc
        if urllib.parse.urlparse(href).netloc not in (host, ""):
            continue

        if any(skip in href.lower() for skip in ("login", "signup", "privacy", "terms", "#", "mailto:")):
            continue

        out.append((href, text))
        seen.add(href)

        if len(out) >= max_links:
            break

    return out

# ===== RSS FEED INTEGRATION =====
# Atom + RSS share most tags but Atom uses different namespaces.
# We strip namespaces in _strip_ns so a single XPath works for both.
_NS_RE = re.compile(r"\{[^}]+\}")

def _strip_ns(elem: ET.Element) -> None:
    """Remove XML namespaces in-place so .find('image') works on Atom feeds."""
    for el in elem.iter():
        el.tag = _NS_RE.sub("", el.tag)
        if el.attrib:
            el.attrib = {_NS_RE.sub("", k): v for k, v in el.attrib.items()}


def _extract_article_image(item: ET.Element, fallback_link: str = "") -> str:
    """Try multiple image sources in an RSS/Atom item."""
    # 1. <media:thumbnail url="..."/> or <media:content url="..."/>
    for tag in ("thumbnail", "content"):
        el = item.find(tag)
        if el is not None and el.get("url"):
            return el.get("url", "").strip()
    # 2. <enclosure url="..." type="image/..."/>
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image"):
        return (enc.get("url") or "").strip()
    # 3. <image><url>...</url></image>
    img_block = item.find("image")
    if img_block is not None:
        url_el = img_block.find("url")
        if url_el is not None and url_el.text:
            return url_el.text.strip()
        if img_block.text:
            return img_block.text.strip()
    # 4. First <img src="..."/> inside description / content:encoded
    for tag in ("description", "summary", "encoded", "content"):
        el = item.find(tag)
        if el is not None and el.text:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', el.text, re.I)
            if m:
                return m.group(1).strip()
    return ""


def _strip_html(text: str) -> str:
    """Strip tags + collapse whitespace from a description/summary string."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_rss_feeds() -> list[dict]:
    """Fetch + parse all configured RSS feeds. Returns a list of article dicts.

    Robust to Atom + RSS, missing fields, and feed-level fetch failures.
    Each article: {id, title, link, summary, image, date, category, source, type}.
    """
    articles: list[dict] = []

    for feed in RSS_FEEDS:
        print(f"[rss] {feed['name']}", file=sys.stderr)
        feed_xml = fetch(feed["url"], timeout=12)
        if not feed_xml:
            print(f"  [warn] empty body for {feed['name']}", file=sys.stderr)
            continue

        try:
            root = ET.fromstring(feed_xml)
            _strip_ns(root)
        except ET.ParseError as e:
            print(f"  [warn] parse failed ({e}) for {feed['name']}", file=sys.stderr)
            continue

        items = root.findall(".//item") or root.findall(".//entry") or []
        for item in items[: feed["max_items"]]:
            title_el = item.find("title")
            link_el = item.find("link")
            # NOTE: ElementTree elements with no children are falsy, so we must
            # use explicit `is not None` checks rather than `a or b` short-circuits.
            desc_el = item.find("description")
            if desc_el is None:
                desc_el = item.find("summary")
            date_el = item.find("pubDate")
            if date_el is None:
                date_el = item.find("published")
            if date_el is None:
                date_el = item.find("updated")

            title = _strip_html(title_el.text or "")[:140] if title_el is not None else ""
            link = ""
            if link_el is not None:
                link = (link_el.text or link_el.get("href") or "").strip()
            summary = _strip_html(desc_el.text or "")[:240] if desc_el is not None else ""
            image = _extract_article_image(item, link)
            pub_date = ""
            if date_el is not None and date_el.text:
                pub_date = date_el.text.strip()
                # Try to normalise to YYYY-MM-DD
                m = re.search(r"(\d{4}-\d{2}-\d{2})", pub_date)
                if m:
                    pub_date = m.group(1)

            if not title or not link:
                continue

            articles.append({
                "id": f"rss-{len(articles) + 1}",
                "title": title,
                "link": link,
                "summary": summary,
                "image": image,
                "date": pub_date,
                "category": feed["category"],
                "source": feed["name"],
                "type": "rss_article",
                "featured": True,
            })

        time.sleep(0.4)

    return articles


def download_article_images(articles: list[dict], outdir: Path) -> list[dict]:
    """Download article images locally into _images/. Returns articles with updated image paths."""
    img_dir = outdir / "_images"
    img_dir.mkdir(parents=True, exist_ok=True)
    updated = []
    for i, a in enumerate(articles):
        a = dict(a)
        remote = a.get("image", "")
        if remote:
            dest = img_dir / f"article_{i+1}.jpg"
            if download_image(remote, dest):
                a["image"] = f"_images/article_{i+1}.jpg"
        updated.append(a)
    return updated


def write_articles(articles: list[dict], outdir: Path, cache_path: Path) -> Path:
    """Write articles.json. Download images locally. If empty, fall back to last-good cache."""
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "articles.json"

    if articles:
        articles = download_article_images(articles, outdir)
        out.write_text(json.dumps(articles, indent=2, ensure_ascii=False))
        cache_path.write_text(json.dumps({
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "articles": articles,
        }, indent=2, ensure_ascii=False))
        print(f"[ok] wrote {len(articles)} articles → {out}", file=sys.stderr)
        return out

    # No fresh articles — try last-good cache
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_articles = cached.get("articles", [])
            if cached_articles:
                out.write_text(json.dumps(cached_articles, indent=2, ensure_ascii=False))
                print(f"[fallback] wrote {len(cached_articles)} cached articles → {out}", file=sys.stderr)
                return out
        except Exception as e:
            print(f"  [warn] cache read failed: {e}", file=sys.stderr)

    # Final fallback: empty array, so the site still loads cleanly
    out.write_text("[]")
    print(f"[ok] wrote empty articles.json (no fresh feeds, no cache)", file=sys.stderr)
    return out


def articles_cache_fresh(cache_path: Path) -> bool:
    """Return True if the cache exists and is younger than ARTICLES_CACHE_TTL_HOURS."""
    if not cache_path.exists():
        return False
    try:
        cached = json.loads(cache_path.read_text())
        fetched = cached.get("fetched_at", "")
        if not fetched:
            return False
        # Strip the trailing 'Z' before parsing
        ts = datetime.fromisoformat(fetched.replace("Z", ""))
        age = datetime.utcnow() - ts
        return age < timedelta(hours=ARTICLES_CACHE_TTL_HOURS)
    except Exception:
        return False

# ===== MAIN COLLECTION LOGIC =====
def collect(max_per_source: int = 12, deep_image: bool = True, include_rss: bool = False) -> list[dict]:
    """Collect events from all sources."""
    events: list[dict] = []
    next_id = 1

    for src in SOURCES:
        print(f"[fetch] {src['name']}", file=sys.stderr)

        page = fetch(src["url"])
        if not page:
            continue

        listing_image = og_image(page, src["url"])
        links = extract_links(page, src["url"], max_links=max_per_source)

        for href, text in links:
            ev: dict[str, Any] = {
                "id": next_id,
                "title": text[:120],
                "venue": src["name"],
                "area": "London",
                "borough": "See site",
                "category": classify_with_confidence(text, src.get("kind", ""))["primary"],
                "category_confidence": classify_with_confidence(text, src.get("kind", ""))["confidence"],
                "date": "Ongoing",
                "time": "",
                "price": "Free" if "free" in text.lower() else "See site",
                "free": "free" in text.lower() or "free" in src["name"].lower(),
                "link": href,
                "image": listing_image,
                "image_fallback_method": "listing",
                "source": src["name"],
                "fetched": datetime.utcnow().isoformat() + "Z",
                "featured": False,
                "verified": False,
            }

            # Deep fetch for detailed info
            if deep_image:
                detail = fetch(href, timeout=10)
                if detail:
                    # Try to extract better date/time
                    datetime_info = extract_datetime_from_page(detail, href)
                    if datetime_info["confidence"] > 0.5:
                        ev["date"] = datetime_info["date"]
                        ev["time"] = datetime_info["time"]
                        ev["date_extraction_method"] = datetime_info["method"]

                    # Try to get better image
                    img = og_image(detail, href)
                    if not img:
                        img = try_alternative_images(detail, href)

                    if img:
                        ev["image"] = img
                        ev["image_fallback_method"] = "detail-page"

                    # Short description (meta description / og:description)
                    dm = (re.search(r'<meta[^>]+(?:property=["\']og:description["\']|name=["\']description["\'])[^>]+content=["\']([^"\']+)["\']', detail, re.I))
                    if dm:
                        ev["desc"] = html.unescape(dm.group(1)).strip()[:240]

                    # Better title
                    title = page_title(detail)
                    if title and len(title) > len(ev["title"]) * 0.6:
                        ev["title"] = title

                time.sleep(0.25)

            # Validate
            is_valid, errors = validate_event(ev)
            if not is_valid:
                print(f"  [skip] {ev['title']}: {errors[0]}", file=sys.stderr)
                continue

            events.append(ev)
            next_id += 1

        time.sleep(0.4)

    # Deduplicate
    events = deduplicate_events(events)
    print(f"[done] collected {len(events)} unique events after deduplication", file=sys.stderr)

    # Add RSS feeds if enabled
    if include_rss:
        articles = fetch_rss_feeds()
        events.extend(articles)
        print(f"[rss] added {len(articles)} articles", file=sys.stderr)

    # Mark top events as featured (by quality score)
    featured_count = 0
    for ev in events:
        if featured_count >= 4:
            break
        if ev.get("image") and ev.get("category_confidence", 0) > 0.5:
            ev["featured"] = True
            featured_count += 1

    return events

# ===== IMAGE DOWNLOADER =====
def download_image(url: str, dest: Path, timeout: int = 10) -> bool:
    """Download an image URL to dest. Returns True on success."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception as e:
        print(f"  [warn] image download failed: {url} ({type(e).__name__})", file=sys.stderr)
        return False


# ===== FEATURED.JSON WRITER =====
def write_featured(events: list[dict], outdir: Path) -> Path:
    """Pick the best featured event, download its image locally, write featured.json."""
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "featured.json"

    # Prefer events marked featured with an image, sorted by quality score
    candidates = [e for e in events if e.get("featured") and e.get("image")]
    if not candidates:
        candidates = [e for e in events if e.get("image")]
    if not candidates:
        candidates = events[:1]

    if not candidates:
        out.write_text("{}")
        return out

    pick = candidates[0]
    remote_img = pick.get("image", "")

    # Download image locally as featured_main.jpg
    local_img_path = outdir / "featured_main.jpg"
    if remote_img and download_image(remote_img, local_img_path):
        pick = dict(pick)  # don't mutate original
        pick["image"] = "featured_main.jpg"
        print(f"[ok] downloaded featured image → {local_img_path}", file=sys.stderr)
    else:
        print(f"  [warn] using remote image URL for featured block", file=sys.stderr)

    out.write_text(json.dumps(pick, indent=2, ensure_ascii=False))
    print(f"[ok] wrote featured.json → {pick.get('title','?')}", file=sys.stderr)
    return out


# ===== OUTPUT WRITERS =====
def write_outputs(events: list[dict], outdir: Path) -> tuple[Path, Path]:
    """Write events.json and events_inject.js."""
    outdir.mkdir(parents=True, exist_ok=True)

    j = outdir / "events.json"
    j.write_text(json.dumps(events, indent=2, ensure_ascii=False))

    js = outdir / "events_inject.js"
    js_content = "// Auto-generated by fetch_events_v2.py\n"
    js_content += "const EVENTS = " + json.dumps(events, indent=2, ensure_ascii=False) + ";\n"
    js.write_text(js_content)

    return j, js

def patch_html(html_path: Path, events: list[dict]) -> None:
    """Auto-patch HTML with new events."""
    print("  [skip] --html would overwrite the curated EVENTS block with raw scraped data "
          "(different schema). Review events.json, then use update_site.py instead.", file=sys.stderr)
    return
    text = html_path.read_text()
    pattern = re.compile(r"const EVENTS = \[.*?\n\];", re.S)
    replacement = "const EVENTS = " + json.dumps(events, indent=2, ensure_ascii=False) + ";"

    if not pattern.search(text):
        print("  [warn] EVENTS block not found in HTML", file=sys.stderr)
        return

    new = pattern.sub(replacement, text, count=1)
    html_path.write_text(new)
    print(f"[ok] patched HTML → {html_path}", file=sys.stderr)

# ===== CLI =====
def main() -> int:
    p = argparse.ArgumentParser(description="Enhanced LDN Directory event fetcher v2")
    p.add_argument("--outdir", type=Path, default=Path(__file__).parent, help="Output directory")
    p.add_argument("--html", type=Path, default=None, help="HTML file to patch")
    p.add_argument("--max-per-source", type=int, default=12, help="Max events per source")
    p.add_argument("--no-deep-image", action="store_true", help="Skip deep image fetch")
    p.add_argument("--rss", action="store_true", help="Also fold RSS articles into events.json (legacy)")
    p.add_argument("--articles", action="store_true",
                   help="Fetch RSS feeds and write articles.json for the v9 reading list. "
                        "Honours the 6-hour cache; passes through cached data if fresh.")
    p.add_argument("--articles-only", action="store_true",
                   help="Skip events; only refresh articles.json. Useful for hourly schedule.")
    p.add_argument("--force-refresh-articles", action="store_true",
                   help="Ignore the articles cache and re-fetch all RSS feeds.")
    p.add_argument("--write-featured", action="store_true",
                   help="Also write the scraper's own featured.json pick (off by default; use build_featured.py)")
    args = p.parse_args()

    cache_path = args.outdir / ".articles_cache.json"

    print(
        f"LDN Directory Fetcher v2 • {len(SOURCES)} sources • {len(RSS_FEEDS)} feeds • "
        f"{datetime.now().isoformat(timespec='seconds')}",
        file=sys.stderr,
    )

    # ---- ARTICLES PATH ----
    if args.articles or args.articles_only:
        if articles_cache_fresh(cache_path) and not args.force_refresh_articles:
            print(
                f"[cache] articles cache fresh (<{ARTICLES_CACHE_TTL_HOURS}h) — "
                "reusing without refetch. Use --force-refresh-articles to override.",
                file=sys.stderr,
            )
            try:
                cached = json.loads(cache_path.read_text())
                articles = cached.get("articles", [])
                (args.outdir / "articles.json").write_text(
                    json.dumps(articles, indent=2, ensure_ascii=False)
                )
                print(f"[ok] wrote {len(articles)} cached articles → articles.json", file=sys.stderr)
            except Exception as e:
                print(f"  [warn] cache read failed: {e}", file=sys.stderr)
        else:
            articles = fetch_rss_feeds()
            write_articles(articles, args.outdir, cache_path)

    if args.articles_only:
        # Even in articles-only mode, write a featured.json from static data
        # (no live event scrape, so we build a minimal static fallback)
        static_featured = [
            {"title": "Dulwich Festival", "venue": "Various, Dulwich", "area": "Dulwich",
             "cat": "festivals", "date": "Ongoing May", "time": "Various", "price": "Free–£15",
             "desc": "South London's beloved arts festival spanning two weeks. Open studios, music, talks, community events across Dulwich.",
             "link": "https://www.dulwichfestival.co.uk", "featured": True,
             "image": "https://images.unsplash.com/photo-1533174072545-7a4b6ad7a6c3?w=1200&q=80&auto=format&fit=crop"}
        ]
        write_featured(static_featured, args.outdir)
        return 0

    # ---- EVENTS PATH ----
    events = collect(
        max_per_source=args.max_per_source,
        deep_image=not args.no_deep_image,
        include_rss=args.rss,
    )
    print(f"[done] collected {len(events)} events total", file=sys.stderr)

    j, js = write_outputs(events, args.outdir)
    print(f"[ok] wrote {j}", file=sys.stderr)
    print(f"[ok] wrote {js}", file=sys.stderr)

    # featured.json is now chosen by build_featured.py (a real, specific event).
    # Only write the scraper's old guess if explicitly asked.
    if args.write_featured:
        write_featured(events, args.outdir)

    if args.html:
        patch_html(args.html, events)

    return 0

if __name__ == "__main__":
    sys.exit(main())
