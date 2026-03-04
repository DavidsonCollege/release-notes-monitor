# Adding New Products to the Release Notes Monitor

## Quick Start

1. Add the product entry to `config/teams.json`
2. Set the product's `icon_url` using Google Favicon API: `https://www.google.com/s2/favicons?domain=DOMAIN&sz=64`
3. If the source requires authentication, add credentials as GitHub Secrets
4. Test by triggering the GitHub Action manually (Actions > check-releases > Run workflow)

## Source Types

### RSS Feed (`rss`)
The simplest source type. Just provide the RSS feed URL.

```json
{
  "id": "product-name",
  "name": "Product Name",
  "domain": "example.com",
  "icon_url": "https://www.google.com/s2/favicons?domain=example.com&sz=64",
  "release_notes_url": "https://example.com/changelog",
  "source": {
    "type": "rss",
    "url": "https://example.com/feed.xml"
  }
}
```

### Web Scrape (`scrape`)
For pages without RSS feeds. Uses CSS selectors to extract content.

```json
{
  "source": {
    "type": "scrape",
    "url": "https://example.com/changelog",
    "item_selector": ".release-item",
    "title_selector": "h3",
    "date_selector": ".date",
    "link_selector": "a"
  }
}
```

### Zendesk Help Center Article (`zendesk_article`)
For release notes published as Zendesk Help Center articles. Supports both public and authenticated access.

```json
{
  "source": {
    "type": "zendesk_article",
    "domain": "vendor.zendesk.com",
    "article_id": "12345678901234",
    "section_anchor": "released_2024",
    "env_email": "VENDOR_ZD_EMAIL",
    "env_password": "VENDOR_ZD_PASSWORD"
  }
}
```

**Important:** The `env_email` and `env_password` fields reference GitHub Secrets names (not the actual credentials).

### Intercom Help Center Article (`intercom_article`)
For release notes published as Intercom Help Center articles. Common across many SaaS products (e.g., Claude, Notion, OpenAI).

```json
{
  "source": {
    "type": "intercom_article",
    "url": "https://support.example.com/en/articles/12345-release-notes"
  }
}
```

The parser detects the standard Intercom page structure: `<h2>` month headings, `<h3>` date headings, and bold `<p>` entry titles with descriptions. Optional overrides: `"month_selector"` and `"date_selector"` for non-standard Intercom layouts.

## Lessons Learned from Adding Kuali Build (Zendesk Source)

### Cloudflare Bot Protection
Many Zendesk instances sit behind Cloudflare's bot detection. Standard Python `requests` will get blocked with 403 errors. The solution is to use the `cloudscraper` library, which mimics a real browser's TLS fingerprint and handles Cloudflare challenges.

**Key insight:** Use a single `cloudscraper` session across all HTTP requests for a given source. Creating separate sessions for login and API calls will fail because the Cloudflare clearance cookie is tied to the session that earned it.

### Three-Strategy Authentication Fallback
The Zendesk article checker uses three strategies in order:

1. **Direct scrape** - Fetch the article URL directly. Works for public articles. If Cloudflare is present, cloudscraper handles the JS challenge automatically.
2. **Session authentication** - If strategy 1 returns a login page, sign in to Zendesk using the same cloudscraper session, then re-fetch the article.
3. **Zendesk API** - Use the authenticated session to call the Zendesk Help Center API endpoint (`/api/v2/help_center/articles/{id}`), which returns clean JSON with the article body.

### Common Pitfalls

- **Don't split sessions:** All three strategies must share the same `cloudscraper.create_scraper()` session. Cloudflare cookies and Zendesk session cookies both need to be present on API calls.
- **Login detection:** Check the page title or look for login form elements to determine if you've hit a login wall rather than the actual article.
- **Article ID:** Found in the Zendesk Help Center article URL (the long numeric string).
- **Section anchors:** If the article has multiple sections (e.g., by year), use `section_anchor` to target the relevant section's HTML id attribute.

### Testing Zendesk Sources Locally
Before committing, you can test by running:
```bash
KUALI_ZD_EMAIL="your@email.com" KUALI_ZD_PASSWORD="yourpass" python scripts/check_releases.py
```

## Dashboard Icon System

### Product Icons
Each product in `teams.json` should have an `icon_url`. The recommended approach is using Google's Favicon API:
```
https://www.google.com/s2/favicons?domain=DOMAIN&sz=64
```

If the icon fails to load, the dashboard automatically shows a letter-based fallback (first letter of the product name in a rounded square).

**Always set `icon_url`** - leaving it empty results in a broken image before the fallback triggers.

### Source Type Badges
Product cards display a small badge indicating the source type. The badge icons are inline SVGs in `docs/index.html`:
- **RSS** - Classic RSS icon (quarter-circle arcs with dot)
- **SCRAPE** - Magnifying glass icon
- **FILTERED** - Text label only

If adding a new source type, you'll need to add a corresponding badge in the `renderProducts()` function in `docs/index.html`.

## Checklist for New Products

- [ ] Product entry added to `config/teams.json` with all required fields
- [ ] `icon_url` set (use Google Favicon API, never leave empty)
- [ ] Source type and configuration correct
- [ ] If authenticated: credentials added as GitHub Secrets
- [ ] If authenticated: `env_email`/`env_password` reference the correct secret names
- [ ] Manual workflow run succeeds (check Actions tab for errors)
- [ ] Product appears on dashboard with correct icon, badge, and feed items
- [ ] Feed preview shows correctly formatted items with dates and links
