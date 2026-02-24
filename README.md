# Release Notes Monitor

A cloud-based tool that watches SaaS product release notes and generates RSS feeds per team. Runs entirely on GitHub Actions (free) — no server needed.

## How It Works

1. **GitHub Actions** runs a Python script every 4 hours on a cron schedule
2. The script checks each product's release notes page (via RSS feed or web scraping)
3. New updates are added to per-team **RSS feeds** served via **GitHub Pages**
4. Teams subscribe to their feed in **Slack**, **Zoom Team Chat**, or any RSS reader

Each RSS entry includes the product icon, name, a brief synopsis, and a link to the full release notes.

## Quick Start (15 minutes)

### 1. Create the Repository

1. Go to [github.com/new](https://github.com/new) and create a new repository
   - Name: `release-notes-monitor` (or whatever you prefer)
   - Visibility: **Public** (required for free GitHub Pages) or Private (requires GitHub Pro/Team)
2. Clone the repo and copy all files from this project into it
3. Push to GitHub:
   ```bash
   git add -A
   git commit -m "Initial setup"
   git push origin main
   ```

### 2. Enable GitHub Pages

1. Go to your repo **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main`
4. Folder: `/public`
5. Click **Save**

Your dashboard will be available at: `https://<your-username>.github.io/release-notes-monitor/`

### 3. Enable GitHub Actions

1. Go to **Actions** tab in your repo
2. If prompted, click **"I understand my workflows, go ahead and enable them"**
3. The workflow will run automatically:
   - Every 4 hours
   - When you push changes to `config/teams.json`
   - When manually triggered via **Actions → Check Release Notes → Run workflow**

### 4. Set Up the Dashboard

1. Visit your GitHub Pages URL (from step 2)
2. Create a **Fine-Grained Personal Access Token**:
   - Go to [github.com/settings/tokens](https://github.com/settings/tokens?type=beta)
   - Click **Generate new token**
   - Name: `Release Notes Dashboard`
   - Repository access: **Only select repositories** → select your release-notes-monitor repo
   - Permissions → Repository permissions → **Contents**: Read and write
   - Click **Generate token** and copy it
3. Enter your token and repo name in the dashboard setup form
4. You're ready to manage teams and products from the dashboard!

### 5. Subscribe in Slack

1. In Slack, go to the channel where you want release notes
2. Type: `/feed subscribe <your-feed-url>`
   - Example: `/feed subscribe https://myorg.github.io/release-notes-monitor/feeds/digital-campus.xml`
3. Slack will check the feed periodically and post new items

> **Note:** The `/feed` command requires the Slack RSS app. Install it from the Slack App Directory if not already available.

### 5b. Subscribe in Zoom Team Chat

1. In Zoom Team Chat, use the RSS connector/integration to subscribe to your feed URL
2. Follow Zoom's documentation for adding RSS feeds to channels

## Managing Teams & Products

### Via the Dashboard (Recommended)

The web dashboard at your GitHub Pages URL lets you:
- Add/remove teams
- Add/remove products within each team
- Configure scraping settings for each product
- View and copy RSS feed URLs

### Via the Config File

You can also directly edit `config/teams.json`. The format is:

```json
{
  "teams": [
    {
      "id": "my-team",
      "name": "My Team",
      "description": "Products managed by my team",
      "products": [
        {
          "id": "product-name",
          "name": "Product Name",
          "domain": "product.com",
          "icon_url": "https://www.google.com/s2/favicons?domain=product.com&sz=64",
          "release_notes_url": "https://product.com/changelog",
          "source": {
            "type": "rss",
            "feed_url": "https://product.com/changelog/feed.xml"
          }
        },
        {
          "id": "another-product",
          "name": "Another Product",
          "domain": "another.com",
          "icon_url": "https://www.google.com/s2/favicons?domain=another.com&sz=64",
          "release_notes_url": "https://another.com/releases",
          "source": {
            "type": "scrape",
            "url": "https://another.com/releases",
            "selector": "article",
            "title_selector": "h2, h3",
            "date_selector": "time, .date",
            "summary_selector": "p"
          }
        }
      ]
    }
  ]
}
```

### Source Types

**RSS/Atom Feed** (`"type": "rss"`):
- Use when the product publishes an RSS or Atom feed
- Most reliable — just set the `feed_url`

**Web Scrape** (`"type": "scrape"`):
- Use when no RSS feed is available
- Requires CSS selectors to identify release entries on the page
- May need adjustment if the product redesigns their page

### Tuning Scrape Selectors

If a scraped product isn't picking up updates correctly:

1. Open the product's release notes page in your browser
2. Right-click → Inspect Element
3. Identify the CSS selector for each release entry (e.g., `article`, `.changelog-entry`, `.release-item`)
4. Identify selectors within each entry for the title, date, and summary
5. Update the product's `source` config in the dashboard or JSON file

## Feed URLs

After setup, your feeds will be available at:

| Feed | URL |
|------|-----|
| All teams combined | `https://<user>.github.io/<repo>/feeds/all.xml` |
| Specific team | `https://<user>.github.io/<repo>/feeds/<team-id>.xml` |
| OPML (all feeds) | `https://<user>.github.io/<repo>/feeds/all-feeds.opml` |

## Customization

### Check Frequency

Edit `.github/workflows/check-releases.yml` and change the cron schedule:

```yaml
schedule:
  - cron: '0 */4 * * *'  # Every 4 hours (default)
  # - cron: '0 */2 * * *'  # Every 2 hours
  # - cron: '0 8,12,16,20 * * *'  # 4 specific times per day
```

### Max Feed Items

Edit `scripts/check_releases.py` and change `MAX_FEED_ITEMS` (default: 50).

## Troubleshooting

- **Actions not running?** Check the Actions tab for errors. Ensure the workflow file is at `.github/workflows/check-releases.yml`
- **Pages not loading?** Verify GitHub Pages is enabled and set to the `main` branch, `/public` folder
- **Scraping not finding items?** Check the CSS selectors. Use browser DevTools to find the right selectors for the product's page structure
- **Rate limited?** The script includes a 1-second delay between products. If you have many products, the 4-hour interval should provide enough spacing.
