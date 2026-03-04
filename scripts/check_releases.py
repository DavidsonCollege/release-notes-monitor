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
import cloudscraper
from bs4 import BeautifulSoup
import feedparser

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "teams.json"
SEEN_FILE = BASE_DIR / "data" / "seen.json"
FEEDS_DIR = BASE_DIR / "docs" / "feeds"
MAX_FEED_ITEMS = 100  # Max items to keep in each team's RSS feed
RECENT_PER_PRODUCT = 5  # Always keep latest N items per product in feed
REQUEST_TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


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


def check_zendesk_api_source(product: dict) -> list[dict]:
    """Check a Zendesk Help Center section for articles via API with basic auth."""
    source = product["source"]
    section_id = source.get("section_id", "")
    domain = source.get("domain", "")
    env_email = source.get("env_email", "")
    env_password = source.get("env_password", "")
    email = os.environ.get(env_email, "")
    password = os.environ.get(env_password, "")

    api_url = f"https://{domain}/api/v2/help_center/en-us/sections/{section_id}/articles.json?sort_by=updated_at&sort_order=desc&per_page=10"
    print(f"  Zendesk API: {api_url}")

    try:
        auth = (email, password) if email and password else None
        resp = requests.get(
            api_url,
            auth=auth,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [WARN] Zendesk API error: {e}")
        return []

    articles = data.get("articles", [])
    items = []
    for article in articles:
        title = article.get("title", "").strip()
        url = article.get("html_url", "")
        updated = article.get("updated_at", "")
        items.append({"title": title, "link": url, "date": updated})

    print(f"  Found {len(items)} articles from API")
    return items


def check_zendesk_article_source(product: dict) -> list[dict]:
    """Check a single Zendesk Help Center article for release items."""
    source = product["source"]
    article_id = source.get("article_id", "")
    domain = source.get("domain", "")
    section_anchor = source.get("section_anchor", "")
    env_email = source.get("env_email", "")
    env_password = source.get("env_password", "")
    email = os.environ.get(env_email, "") if env_email else ""
    password = os.environ.get(env_password, "") if env_password else ""

    if not article_id or not domain:
        print(f"  [ERROR] Missing article_id or domain for zendesk_article source")
        return []

    article_url = f"https://{domain}/hc/en-us/articles/{article_id}"
    api_url = f"https://{domain}/api/v2/help_center/en-us/articles/{article_id}.json"

    body_html = ""
    updated_at = ""

    # Browser-like headers to avoid bot detection
    browser_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    # Create cloudscraper session (bypasses Cloudflare, reused across all strategies)
    scraper = cloudscraper.create_scraper()

    # Strategy 1: Try direct HTML scrape (works if article is public)
    try:
        print(f"  Fetching article page: {article_url}")
        direct_resp = scraper.get(article_url, timeout=REQUEST_TIMEOUT)
        direct_resp.raise_for_status()
        print(f"  Direct scrape response: {direct_resp.status_code}, length={len(direct_resp.content)}")
        page_soup = BeautifulSoup(direct_resp.content, "html.parser")
        title_tag = page_soup.title
        print(f"  Page title: {title_tag.string.strip() if title_tag and title_tag.string else 'NO TITLE'}")
        body_el = page_soup.select_one(".article-body, [itemprop='articleBody'], article")
        if body_el:
            body_html = str(body_el)
            updated_at = datetime.now(timezone.utc).isoformat()
            print(f"  Got article content via direct HTML scrape ({len(body_html)} chars)")
        else:
            # Debug: show what selectors are available
            all_classes = set()
            for el in page_soup.find_all(True):
                for c in el.get("class", []):
                    all_classes.add(c)
            print(f"  [WARN] No article body found. Classes on page: {sorted(all_classes)[:30]}")
    except Exception as e:
        print(f"  [WARN] Direct HTML scrape failed: {e}")

    # Strategy 2: If direct scrape failed, try session auth + API
    if not body_html and email and password:
        session = scraper  # Reuse cloudscraper session to bypass Cloudflare
        try:
            signin_url = f"https://{domain}/hc/en-us/signin"
            print(f"  Signing in to {domain}...")
            signin_resp = session.get(signin_url, timeout=REQUEST_TIMEOUT)
            signin_soup = BeautifulSoup(signin_resp.content, "html.parser")
            csrf_input = signin_soup.find("input", {"name": "authenticity_token"})
            csrf_token = csrf_input.get("value", "") if csrf_input else ""
            login_form = signin_soup.find("form")
            form_action = login_form.get("action", "") if login_form else ""
            if form_action and not form_action.startswith("http"):
                form_action = f"https://{domain}{form_action}"
            if not form_action:
                form_action = f"https://{domain}/access/login"
            login_data = {"user[email]": email, "user[password]": password}
            if csrf_token:
                login_data["authenticity_token"] = csrf_token
            login_resp = session.post(form_action, data=login_data, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            print(f"  Login response: {login_resp.status_code}")
        except Exception as e:
            print(f"  [WARN] Session login failed: {e}")

        # Try API with session cookies
        print(f"  Zendesk Article API (session): {api_url}")
        try:
            resp = session.get(api_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            article = data.get("article", {})
            body_html = article.get("body", "")
            article_url = article.get("html_url", "") or article_url
            updated_at = article.get("updated_at", "")
            print(f"  Got article content via API")
        except Exception as e:
            print(f"  [WARN] API with session failed: {e}")

    # Strategy 3: If still no content, try API without auth (some are public)
    if not body_html:
        print(f"  Trying API without auth: {api_url}")
        try:
            resp = scraper.get(api_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            article = data.get("article", {})
            body_html = article.get("body", "")
            article_url = article.get("html_url", "") or article_url
            updated_at = article.get("updated_at", "")
            print(f"  Got article content via unauthenticated API")
        except Exception as e:
            print(f"  [WARN] Unauthenticated API also failed: {e}")

    if not body_html:
        print(f"  [ERROR] All strategies failed for article {article_id}")
        return []

    soup = BeautifulSoup(body_html, "html.parser")

    items = []

    if section_anchor:
        anchor_el = soup.find(id=section_anchor)
        if not anchor_el:
            anchor_el = soup.find("a", {"name": section_anchor})
        if not anchor_el:
            print(f"  [WARN] Section anchor '{section_anchor}' not found in article {article_id}")
            return []

        section_heading = anchor_el
        if section_heading.name != "h2":
            section_heading = anchor_el.find_parent("h2") or anchor_el

        current = section_heading.find_next_sibling()
        while current:
            if current.name == "h2":
                break
            if current.name == "h3":
                title = current.get_text(strip=True)
                link_el = current.find("a", href=True)
                link = link_el["href"] if link_el else ""
                if not link:
                    item_id = current.get("id", "")
                    link = f"{article_url}#{item_id}" if item_id else article_url

                desc_parts = []
                desc_el = current.find_next_sibling()
                while desc_el and desc_el.name not in ("h2", "h3"):
                    if desc_el.name == "p":
                        desc_parts.append(desc_el.get_text(strip=True))
                    desc_el = desc_el.find_next_sibling()
                description = " ".join(desc_parts)

                items.append({"title": title, "link": link, "date": updated_at, "description": description})

            current = current.find_next_sibling()
    else:
        for h3 in soup.find_all("h3"):
            title = h3.get_text(strip=True)
            link_el = h3.find("a", href=True)
            link = link_el["href"] if link_el else article_url
            items.append({"title": title, "link": link, "date": updated_at})

    return items


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
    from urllib.parse import urljoin

    source = product["source"]
    url = source["url"]
    print(f"  Scraping page: {url}")

    resp = make_request(url)
    if not resp:
        return []

    # Check if page is mostly empty (JS-rendered)
    soup = BeautifulSoup(resp.content, "html.parser")

    # Remove script, style, nav, footer, header elements to focus on content
    for tag in soup.select("script, style, nav, footer, header, noscript, svg, iframe"):
        tag.decompose()

    body_text = clean_text(soup.get_text()) if soup.body else ""
    if len(body_text) < 100:
        print(f"  [WARN] Page appears JS-rendered or empty ({len(body_text)} chars). Scraping may fail.")

    items = []

    # Strategy 1: Try configured selector
    selector = source.get("selector", "article")
    elements = soup.select(selector)
    if elements:
        print(f"  Found {len(elements)} elements with selector: {selector}")

    # Strategy 2: Common changelog patterns
    if not elements:
        for fallback_selector in ["article", ".changelog-entry", ".release-note", ".post",
                                  "section:not(:empty)", ".entry", ".update",
                                  "[class*='release']", "[class*='changelog']", "[class*='update']"]:
            elements = soup.select(fallback_selector)
            if elements:
                print(f"  Fallback selector matched: {fallback_selector} ({len(elements)} elements)")
                break

    # Strategy 3: Headings as entry markers
    if not elements:
        elements = soup.select("h2, h3")
        if elements:
            print(f"  Using headings as entries ({len(elements)} found)")

    # Strategy 4: Links that look like changelog entries
    if not elements:
        all_links = soup.select("a[href]")
        version_pattern = re.compile(r'(v?\d+\.\d+|release|update|version|changelog|what.?s.new)', re.I)
        elements = [a for a in all_links if version_pattern.search(a.get_text() + " " + a.get("href", ""))]
        if elements:
            print(f"  Found {len(elements)} version-like links")

    if not elements:
        print(f"  [WARN] No elements found on page. Site may require JavaScript.")
        return []

    for el in elements[:10]:
        # Extract title
        title_sel = source.get("title_selector", "h2, h3")
        title_el = el.select_one(title_sel) if title_sel else None
        if title_el:
            title = clean_text(title_el.get_text())
        elif el.name in ("a", "h2", "h3", "h4", "td", "strong"):
            title = clean_text(el.get_text()[:150])
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
        # Also check for time/datetime attributes
        if not date_text:
            time_el = el.select_one("time[datetime]")
            if time_el:
                date_text = time_el.get("datetime", "")

        # Extract summary
        summary_sel = source.get("summary_selector", "p")
        summary = ""
        if summary_sel:
            summary_els = el.select(summary_sel)
            if summary_els:
                summary_parts = [clean_text(s.get_text()) for s in summary_els[:3]]
                summary = " ".join(summary_parts)
        if not summary:
            # Get next sibling text if element is a heading
            if el.name in ("h2", "h3", "h4", "strong"):
                sibling = el.find_next_sibling()
                if sibling and sibling.name in ("p", "ul", "div"):
                    summary = clean_text(sibling.get_text()[:500])
        if not summary:
            summary = clean_text(el.get_text()[:500])
        # Don't use the title as the summary
        if summary == title:
            summary = ""
        summary = truncate_text(summary)

        # Extract link
        link = product["release_notes_url"]
        if el.name == "a" and el.get("href"):
            href = el.get("href", "")
            if href.startswith(("http://", "https://")):
                link = href
            elif href.startswith("/"):
                link = urljoin(url, href)
        else:
            link_el = el.select_one("a[href]")
            if link_el:
                href = link_el.get("href", "")
                if href.startswith(("http://", "https://")):
                    link = href
                elif href.startswith("/"):
                    link = urljoin(url, href)

        # Skip generic/navigation items
        skip_words = {"menu", "navigation", "sidebar", "footer", "header", "cookie",
                      "privacy", "sign in", "log in", "subscribe", "contact", "about us"}
        title_lower = title.lower()
        if any(w in title_lower for w in skip_words):
            continue

        # Skip very long titles (likely scraped whole paragraphs)
        if len(title) > 200:
            title = title[:197] + "..."

        items.append({
            "title": title,
            "link": link,
            "summary": summary,
            "date": date_text or datetime.now(timezone.utc).isoformat(),
        })

    print(f"  Scraped {len(items)} items from page")
    return items


def check_nextjs_blog_source(product: dict) -> list[dict]:
    """Extract blog posts from a Next.js site's __NEXT_DATA__ JSON."""
    from urllib.parse import urljoin
    import json as _json

    source = product["source"]
    url = source["url"]
    posts_path = source.get("posts_path", "props.pageProps.posts")
    title_key = source.get("title_key", "title")
    date_key = source.get("date_key", "publishDate")
    slug_key = source.get("slug_key", "slug")
    slug_prefix = source.get("slug_prefix", "")
    print(f"  Next.js blog: {url}")

    resp = make_request(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    script_tag = soup.find("script", id="__NEXT_DATA__")
    if not script_tag or not script_tag.string:
        print("  [WARN] No __NEXT_DATA__ found. Site may not be Next.js.")
        return []

    try:
        next_data = _json.loads(script_tag.string)
    except Exception as e:
        print(f"  [WARN] Failed to parse __NEXT_DATA__: {e}")
        return []

    # Navigate the JSON path to find posts
    obj = next_data
    for key in posts_path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key, {})
        else:
            print(f"  [WARN] Could not traverse path: {posts_path}")
            return []

    if not isinstance(obj, list):
        print(f"  [WARN] Path {posts_path} did not resolve to a list")
        return []

    print(f"  Found {len(obj)} posts in __NEXT_DATA__")
    items = []
    for post in obj[:10]:
        title = post.get(title_key, "").strip()
        if not title:
            continue
        date_val = post.get(date_key, "")
        slug = post.get(slug_key, "")
        link = urljoin(url, slug_prefix + slug) if slug else url
        items.append({
            "title": title,
            "link": link,
            "summary": "",
            "date": date_val or datetime.now(timezone.utc).isoformat(),
        })

    print(f"  Extracted {len(items)} items")
    return items


def apply_keyword_filters(items: list[dict], product: dict) -> list[dict]:
    """Filter items based on include/exclude keyword rules."""
    filters = product.get("filter", {})
    include_keywords = [k.lower() for k in filters.get("include", [])]
    exclude_keywords = [k.lower() for k in filters.get("exclude", [])]

    if not include_keywords and not exclude_keywords:
        return items

    filtered = []
    for item in items:
        title_lower = item["title"].lower()
        summary_lower = item.get("summary", "").lower()
        text = title_lower + " " + summary_lower

        # Include filter: item must match at least one keyword
        if include_keywords:
            if not any(kw in text for kw in include_keywords):
                print(f"    SKIP (no include match): {item['title'][:60]}")
                continue

        # Exclude filter: item must not match any keyword
        if exclude_keywords:
            if any(kw in text for kw in exclude_keywords):
                print(f"    SKIP (exclude match): {item['title'][:60]}")
                continue

        filtered.append(item)

    if len(filtered) != len(items):
        print(f"  Keyword filter: {len(items)} -> {len(filtered)} items")
    return filtered




def check_intercom_article_source(product: dict) -> list[dict]:
    """
    Parse an Intercom Help Center article that contains structured release notes.

    Expected page structure:
      <h2>Month Year</h2>           - month section heading
      <h3>Month Day, Year</h3>      - date heading for entries that day
      <p><b>Feature title</b></p>   - individual entry title (bold paragraph)
      <p>Description text...</p>    - entry description
      <ul><li>...</li></ul>         - optional related links
      <hr/>                         - separator between month sections

    This is common across Intercom-based help centers (e.g., support.claude.com,
    support.notion.so, help.openai.com, etc.).
    """
    source = product["source"]
    url = source["url"]

    # Allow overriding the heading selectors for different Intercom layouts
    month_selector = source.get("month_selector", "h2")
    date_selector = source.get("date_selector", "h3")

    print(f"  Intercom article: {url}")

    # Use cloudscraper to bypass bot detection (e.g. OpenAI blocks basic requests)
    scraper = cloudscraper.create_scraper()
    try:
        resp = scraper.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")

    # Find the article body - Intercom uses various containers
    article_body = (
        soup.select_one("[class*='article__body']")
        or soup.select_one("article")
        or soup.select_one(".intercom-interblocks-article-body")
        or soup.select_one("[itemprop='articleBody']")
        or soup.select_one("main")
    )

    if not article_body:
        article_body = soup.body or soup
        for tag in article_body.select("script, style, nav, footer, header, noscript, svg, iframe"):
            tag.decompose()

    body_text = clean_text(article_body.get_text()) if article_body else ""
    if len(body_text) < 100:
        print(f"  [WARN] Article body appears empty ({len(body_text)} chars). Page may require JavaScript.")
        return []

    print(f"  Article body: {len(body_text)} chars")

    items = []
    current_date = None

    # Date patterns to detect date headings (e.g., "March 2, 2026")
    date_pattern = re.compile(
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+\d{1,2},?\s+\d{4}',
        re.IGNORECASE
    )
    # Month pattern to detect month-only headings (e.g., "March 2026")
    month_pattern = re.compile(
        r'^(?:January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+\d{4}$',
        re.IGNORECASE
    )

    for el in article_body.find_all(True):
        # Skip elements nested inside list items - these are reference links
        if el.find_parent(["li", "ul", "ol"]):
            continue

        tag_name = el.name
        text = clean_text(el.get_text())

        if not text or len(text) < 2:
            continue

        # Detect month headings (h2) - skip these as entries
        if tag_name == month_selector and month_pattern.match(text):
            continue

        # Detect date headings (h3) - set the current_date context
        if tag_name == date_selector and date_pattern.search(text):
            date_match = date_pattern.search(text)
            if date_match:
                try:
                    date_str = date_match.group(0).replace(",", "")
                    current_date = datetime.strptime(date_str, "%B %d %Y").replace(
                        tzinfo=timezone.utc
                    ).isoformat()
                except ValueError:
                    current_date = datetime.now(timezone.utc).isoformat()
            continue

        # Detect entry titles - bold text inside a paragraph (Intercom pattern)
        if tag_name == "p":
            bold_el = el.find(["b", "strong"], recursive=False)
            if not bold_el:
                continue

            title = clean_text(bold_el.get_text())

            # Skip if the paragraph is just a bold link (reference links)
            if el.find("a", recursive=False):
                continue

            if len(title) < 3 or len(title) > 200:
                continue

            # Skip sub-feature bullets that end with ":"
            if title.endswith(":"):
                continue

            # Skip navigation-like items and generic short phrases
            skip_words = {"menu", "navigation", "sidebar", "footer", "header",
                          "cookie", "privacy", "sign in", "log in", "subscribe",
                          "contact", "about us", "our blog post", "learn more",
                          "read more", "see more", "click here"}
            title_lower = title.lower()
            if any(w in title_lower for w in skip_words):
                continue

            # Skip very short generic titles
            if len(title) < 8 and not any(c.isdigit() for c in title):
                continue

            # Gather description from following siblings
            desc_parts = []
            sibling = el.find_next_sibling()
            while sibling:
                if sibling.name in (month_selector, date_selector):
                    break
                if sibling.name == "hr":
                    break
                # Stop if we hit another top-level bold paragraph (next entry)
                if sibling.name == "p":
                    next_bold = sibling.find(["b", "strong"], recursive=False)
                    if next_bold and not sibling.find("a", recursive=False):
                        next_title = clean_text(next_bold.get_text())
                        if len(next_title) > 3 and not next_title.endswith(":"):
                            break
                if sibling.name in ("p", "ul", "ol"):
                    desc_parts.append(clean_text(sibling.get_text()))
                sibling = sibling.find_next_sibling()

            summary = truncate_text(" ".join(desc_parts))
            link = product.get("release_notes_url", url)

            items.append({
                "title": title,
                "link": link,
                "summary": summary,
                "date": current_date or datetime.now(timezone.utc).isoformat(),
            })

    print(f"  Parsed {len(items)} release entries from Intercom article")
    return items

def check_product(product: dict) -> list[dict]:
    """Check a product for new releases based on its source type."""
    source_type = product["source"]["type"]

    if source_type == "rss":
        items = check_rss_source(product)
    elif source_type == "scrape":
        items = check_scrape_source(product)
    elif source_type == "zendesk_api":
        items = check_zendesk_api_source(product)
    elif source_type == "nextjs_blog":
        items = check_nextjs_blog_source(product)
    elif source_type == "zendesk_article":
        return check_zendesk_article_source(product)
    elif source_type == "intercom_article":
        items = check_intercom_article_source(product)
    else:
        print(f"  [WARN] Unknown source type: {source_type}")
        return []

    return apply_keyword_filters(items, product)


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

        # Rich description with product icon and summary
            icon_url = item_data.get("icon_url", "")
            summary = item_data.get("summary", "")
            description_html = (
                f'<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;'
                f'max-width:600px;padding:12px;border:1px solid #e0e0e0;border-radius:8px;'
                f'background:#ffffff;">'
                f'<div style="display:flex;align-items:center;margin-bottom:8px;">'
                f'<img src="{icon_url}" alt="{product_name}" width="32" height="32" '
                f'style="border-radius:6px;margin-right:10px;"/>'
                f'<strong style="font-size:15px;color:#1a1a1a;">{product_name}</strong>'
                f'</div>'
                f'{f"<p style=\"margin:0 0 10px;color:#444;font-size:14px;line-height:1.5;\">{summary}</p>" if summary else ""}'
                f'<a href="{item_data["link"]}" style="color:#3b82f6;font-size:13px;'
                f'text-decoration:none;">View release notes \u2192</a>'
                f'</div>'
            )
            SubElement(item, "description").text = description_html

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
            '<?xml version="1.0" ?>',
            '<?xml version="1.0" encoding="UTF-8"?>'
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
            '<?xml version="1.0" ?>',
            '<?xml version="1.0" encoding="UTF-8"?>'
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
        recent_items = []  # Always-included latest items per product

        # Expand products with subproducts into individual entries
        expanded_products = []
        for p in products:
            if "subproducts" in p:
                for sub in p["subproducts"]:
                    expanded = {
                        "id": sub["id"],
                        "name": sub.get("name", sub["id"]),
                        "domain": p.get("domain", ""),
                        "icon_url": p.get("icon_url", ""),
                        "release_notes_url": sub.get("release_notes_url", p.get("release_notes_url", "")),
                        "source": sub["source"],
                    }
                    if "filter" in sub:
                        expanded["filter"] = sub["filter"]
                    elif "filter" in p:
                        expanded["filter"] = p["filter"]
                    expanded_products.append(expanded)
            else:
                expanded_products.append(p)
        products = expanded_products

        for product in products:
            product_id = product["id"]
            product_name = product["name"]
            print(f"\n  Checking: {product_name}")

            try:
                raw_items = check_product(product)
                print(f"  Found {len(raw_items)} items from source")

                if product_id not in seen[team_id]:
                    seen[team_id][product_id] = []

                # Always enrich the latest items for the feed
                for i, raw_item in enumerate(raw_items[:RECENT_PER_PRODUCT]):
                    item_id = generate_item_id(product_id, raw_item["title"], raw_item["link"])
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
                    recent_items.append(enriched_item)

                    # Track new vs seen
                    if item_id not in seen[team_id][product_id]:
                        seen[team_id][product_id].append(item_id)
                        new_team_items.append(enriched_item)
                        print(f"    NEW: {raw_item['title'][:80]}")
                    else:
                        print(f"    OK: {raw_item['title'][:80]}")

                # Also track remaining items beyond the top 5 for seen
                for raw_item in raw_items[RECENT_PER_PRODUCT:]:
                    item_id = generate_item_id(product_id, raw_item["title"], raw_item["link"])
                    if item_id not in seen[team_id][product_id]:
                        seen[team_id][product_id].append(item_id)

                # Keep seen list from growing unbounded
                seen[team_id][product_id] = seen[team_id][product_id][-200:]

            except Exception as e:
                print(f"  [ERROR] Failed to check {product_name}: {e}")
                traceback.print_exc()

            # Be polite to servers
            time.sleep(1)

        # Combine: recent items (always fresh) + existing history, deduplicate
        all_items = recent_items + existing_items
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
