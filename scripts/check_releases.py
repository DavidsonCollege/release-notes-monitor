#!/usr/bin/env python3
"""
Release Notes Monitor
Checks SaaS product release notes pages for updates and generates RSS feeds per team.
Designed to run via GitHub Actions on a cron schedule.
"""

import json
import hashlib
import os
import sys
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring, ElementTree
from xml.dom import minidom
import traceback

import requests
from bs4 import BeautifulSoup
import feedparser

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "teams.json"
SEEN_FILE = BASE_DIR / "data" / "seen.json"
FEEDS_DIR = BASE_DIR / "public" / "feeds"
MAX_FEED_ITEMS = 50  # Max items to keep in each team's RSS feed
REQUEST_TIMEOUT = 30
USER_AGENT = "ReleaseNotesMonitor/1.0 (+https://github.com)"


def load_json(path: Path) -> dict:
    """Load a JSON file, returning empty dict if not found."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict):
    """Save data to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def make_request(url: str) -> requests.Response | None:
    """Make an HTTP GET request with error handling."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return None


def generate_item_id(product_id: str, title: str, link: str = "") -> str:
    """Generate a unique, stable ID for a release item."""
    raw = f"{product_id}:{title}:{link}".strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def truncate_text(text: str, max_length: int = 300) -> str:
    """Truncate text to max_length, breaking at word boundary."""
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= max_length:
        return text
    truncated = text[:max_length].rsplit(" ", 1)[0]
    return truncated + "..."


def clean_text(text: str) -> str:
    """Clean up scraped text."""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text


# --- Source Handlers ---

def check_rss_source(product: dict) -> list[dict]:
    """Check an RSS/Atom feed for new items."""
    feed_url = product["source"]["feed_url"]
    print(f"  Checking RSS feed: {feed_url}")

    resp = make_request(feed_url)
    if not resp:
        return []

    feed = feedparser.parse(resp.content)
    items = []

    for entry in feed.entries[:10]:  # Only check latest 10 entries
        title = clean_text(getattr(entry, "title", "Untitled"))
        link = getattr(entry, "link", product["release_notes_url"])
        summary = clean_text(getattr(entry, "summary", ""))
        if not summary and hasattr(entry, "description"):
            summary = clean_text(entry.description)

        # Strip HTML from summary
        if summary and ("<" in summary):
            soup = BeautifulSoup(summary, "html.parser")
            summary = clean_text(soup.get_text())

        summary = truncate_text(summary)

        # Parse date
        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc).isoformat()

        items.append({
            "title": title,
            "link": link,
            "summary": summary,
            "date": pub_date or datetime.now(timezone.utc).isoformat(),
        })

    return items


def check_scrape_source(product: dict) -> list[dict]:
    """Scrape a web page for release notes."""
    source = product["source"]
    url = source["url"]
    print(f"  Scraping page: {url}")

    resp = make_request(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    items = []

    # Strategy 1: Try to find items using the configured selector
    selector = source.get("selector", "article")
    elements = soup.select(selector)

    if not elements:
        # Strategy 2: Fall back to looking for common changelog patterns
        for fallback_selector in ["article", ".changelog-entry", ".release-note",
                                   ".post", "section", ".entry", ".update"]:
            elements = soup.select(fallback_selector)
            if elements:
                break

    if not elements:
        # Strategy 3: Use headings as entry markers
        elements = soup.select("h2, h3")

    for el in elements[:10]:  # Process up to 10 entries
        # Extract title
        title_sel = source.get("title_selector", "h2, h3")
        title_el = el.select_one(title_sel) if title_sel else None
        if title_el:
            title = clean_text(title_el.get_text())
        else:
            title = clean_text(el.get_text()[:150])

        if not title or len(title) < 3:
            continue

        # Extract date
        date_sel = source.get("date_selector")
        date_text = None
        if date_sel:
            date_el = el.select_one(date_sel)
            if date_el:
                date_text = clean_text(date_el.get_text())

        # Extract summary
        summary_sel = source.get("summary_selector", "p")
        summary = ""
        if summary_sel:
            summary_els = el.select(summary_sel)
            if summary_els:
                summary_parts = [clean_text(s.get_text()) for s in summary_els[:3]]
                summary = " ".join(summary_parts)
        if not summary:
            summary = clean_text(el.get_text()[:500])

        summary = truncate_text(summary)

        # Extract link
        link_el = el.select_one("a[href]")
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("/"):
                # Make absolute URL
                from urllib.parse import urljoin
                href = urljoin(url, href)
            link = href
        else:
            link = product["release_notes_url"]

        # Skip if title is too generic or looks like navigation
        skip_words = {"menu", "navigation", "sidebar", "footer", "header", "cookie", "privacy"}
        if any(w in title.lower() for w in skip_words):
            continue

        items.append({
            "title": title,
            "link": link,
            "summary": summary,
            "date": date_text or datetime.now(timezone.utc).isoformat(),
        })

    return items


def check_product(product: dict) -> list[dict]:
    """Check a product for new releases based on its source type."""
    source_type = product["source"]["type"]

    if source_type == "rss":
        return check_rss_source(product)
    elif source_type == "scrape":
        return check_scrape_source(product)
    else:
        print(f"  [WARN] Unknown source type: {source_type}")
        return []


# --- RSS Feed Generation ---

def generate_rss_feed(team: dict, all_items: list[dict], base_url: str) -> str:
    """Generate an RSS 2.0 XML feed for a team."""
    rss = Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")

    channel = SubElement(rss, "channel")

    # Channel metadata
    SubElement(channel, "title").text = f"{team['name']} - Release Notes"
    SubElement(channel, "description").text = (
        team.get("description", f"Release notes for products managed by {team['name']}")
    )
    feed_link = f"{base_url}/feeds/{team['id']}.xml"
    SubElement(channel, "link").text = feed_link
    SubElement(channel, "language").text = "en-us"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    # Atom self link
    atom_link = SubElement(channel, "atom:link")
    atom_link.set("href", feed_link)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # Sort items by date (newest first), limit to MAX_FEED_ITEMS
    sorted_items = sorted(all_items, key=lambda x: x.get("date", ""), reverse=True)
    sorted_items = sorted_items[:MAX_FEED_ITEMS]

    for item_data in sorted_items:
        item = SubElement(channel, "item")

        product_name = item_data.get("product_name", "")
        title = item_data["title"]
        display_title = f"{product_name} - {title}" if product_name else title

        SubElement(item, "title").text = display_title
        SubElement(item, "link").text = item_data["link"]

        # Plain-text description (Slack strips HTML from RSS descriptions)
        summary = item_data.get("summary", "")
        description_text = f"{product_name}: {summary}" if summary else product_name

        SubElement(item, "description").text = description_text

        # GUID
        guid = SubElement(item, "guid")
        guid.set("isPermaLink", "false")
        guid.text = item_data.get("id", generate_item_id(
            item_data.get("product_id", ""), title, item_data["link"]
        ))

        # Publication date
        if item_data.get("date"):
            try:
                dt = datetime.fromisoformat(item_data["date"].replace("Z", "+00:00"))
                SubElement(item, "pubDate").text = dt.strftime(
                    "%a, %d %b %Y %H:%M:%S +0000"
                )
            except (ValueError, AttributeError):
                SubElement(item, "pubDate").text = datetime.now(timezone.utc).strftime(
                    "%a, %d %b %Y %H:%M:%S +0000"
                )

    # Pretty print
    xml_str = tostring(rss, encoding="unicode", xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str

    try:
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent="  ", encoding=None).replace(
            '<?xml version="1.0" ?>', '<?xml version="1.0" encoding="UTF-8"?>'
        )
    except Exception:
        return xml_str


def generate_opml(teams: list[dict], base_url: str) -> str:
    """Generate an OPML file listing all team feeds for easy subscription."""
    opml = Element("opml", version="2.0")
    head = SubElement(opml, "head")
    SubElement(head, "title").text = "Release Notes Monitor - All Feeds"
    SubElement(head, "dateCreated").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    body = SubElement(opml, "body")
    for team in teams:
        outline = SubElement(body, "outline")
        outline.set("text", f"{team['name']} Release Notes")
        outline.set("title", f"{team['name']} Release Notes")
        outline.set("type", "rss")
        outline.set("xmlUrl", f"{base_url}/feeds/{team['id']}.xml")
        outline.set("htmlUrl", base_url)

    xml_str = tostring(opml, encoding="unicode", xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str

    try:
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent="  ", encoding=None).replace(
            '<?xml version="1.0" ?>', '<?xml version="1.0" encoding="UTF-8"?>'
        )
    except Exception:
        return xml_str


# --- Main ---

def main():
    print("=" * 60)
    print("Release Notes Monitor")
    print(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Load config and seen data
    config = load_json(CONFIG_FILE)
    seen = load_json(SEEN_FILE)
    teams = config.get("teams", [])

    if not teams:
        print("No teams configured. Exiting.")
        sys.exit(0)

    # Get base URL from environment or config
    base_url = os.environ.get("BASE_URL", config.get("base_url", "https://example.github.io/release-notes-monitor/"))
    base_url = base_url.rstrip("/")

    # Ensure feeds directory exists
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)

    new_items_total = 0

    for team in teams:
        team_id = team["id"]
        team_name = team["name"]
        products = team.get("products", [])

        print(f"\n--- Team: {team_name} ({len(products)} products) ---")

        # Initialize seen data for this team
        if team_id not in seen:
            seen[team_id] = {}

        # Load existing feed items (to preserve history)
        existing_feed_path = FEEDS_DIR / f"{team_id}.json"
        existing_items = []
        if existing_feed_path.exists():
            try:
                with open(existing_feed_path, "r", encoding="utf-8") as f:
                    existing_items = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing_items = []

        new_team_items = []

        for product in products:
            product_id = product["id"]
            product_name = product["name"]
            print(f"\n  Checking: {product_name}")

            try:
                raw_items = check_product(product)
                print(f"  Found {len(raw_items)} items from source")

                if product_id not in seen[team_id]:
                    seen[team_id][product_id] = []

                for raw_item in raw_items:
                    item_id = generate_item_id(product_id, raw_item["title"], raw_item["link"])

                    if item_id not in seen[team_id][product_id]:
                        # New item!
                        seen[team_id][product_id].append(item_id)
                        enriched_item = {
                            "id": item_id,
                            "product_id": product_id,
                            "product_name": product_name,
                            "icon_url": product.get("icon_url", ""),
                            "title": raw_item["title"],
                            "link": raw_item["link"],
                            "summary": raw_item.get("summary", ""),
                            "date": raw_item.get("date", datetime.now(timezone.utc).isoformat()),
                        }
                        new_team_items.append(enriched_item)
                        print(f"    NEW: {raw_item['title'][:80]}")

                # Keep seen list from growing unbounded
                seen[team_id][product_id] = seen[team_id][product_id][-200:]

            except Exception as e:
                print(f"  [ERROR] Failed to check {product_name}: {e}")
                traceback.print_exc()

            # Be polite to servers
            time.sleep(1)

        # Combine new items with existing, deduplicate, and limit
        all_items = new_team_items + existing_items
        seen_ids = set()
        deduped_items = []
        for item in all_items:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                deduped_items.append(item)
        deduped_items = deduped_items[:MAX_FEED_ITEMS]

        # Save items data (JSON backup for persistence)
        save_json(existing_feed_path, deduped_items)

        # Generate RSS feed
        rss_xml = generate_rss_feed(team, deduped_items, base_url)
        rss_path = FEEDS_DIR / f"{team_id}.xml"
        with open(rss_path, "w", encoding="utf-8") as f:
            f.write(rss_xml)

        print(f"\n  Team '{team_name}': {len(new_team_items)} new items, {len(deduped_items)} total in feed")
        new_items_total += len(new_team_items)

    # Generate OPML for easy subscription
    opml_xml = generate_opml(teams, base_url)
    opml_path = FEEDS_DIR / "all-feeds.opml"
    with open(opml_path, "w", encoding="utf-8") as f:
        f.write(opml_xml)

    # Generate a master feed combining all teams
    all_team_items = []
    for team in teams:
        team_feed_path = FEEDS_DIR / f"{team['id']}.json"
        if team_feed_path.exists():
            try:
                with open(team_feed_path, "r", encoding="utf-8") as f:
                    all_team_items.extend(json.load(f))
            except (json.JSONDecodeError, IOError):
                pass

    master_team = {
        "id": "all",
        "name": "All Teams",
        "description": "Combined release notes from all teams",
    }
    master_rss = generate_rss_feed(master_team, all_team_items, base_url)
    with open(FEEDS_DIR / "all.xml", "w", encoding="utf-8") as f:
        f.write(master_rss)

    # Save seen data
    save_json(SEEN_FILE, seen)

    print(f"\n{'=' * 60}")
    print(f"Done! {new_items_total} new items found across all teams.")
    print(f"Feeds written to: {FEEDS_DIR}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
