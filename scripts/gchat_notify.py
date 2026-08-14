"""
Google Chat webhook notifications for Release Notes Monitor.

Sends Card v2 formatted messages to per-team Google Chat spaces via incoming
webhooks.  Each release-note item is rendered as a card with a product icon,
bold product name, linked title, and summary.

Setup:
  1. Open the Google Chat space where you want notifications.
  2. Click the space name → Apps & integrations → + Add webhooks.
  3. Give it a name (e.g. "Release Notes Monitor") and optional avatar URL.
  4. Copy the webhook URL and add it to teams.json as "gchat_webhook".

No environment variables required — everything is configured in teams.json.
"""

import json
import hashlib
from datetime import datetime, timezone

import requests

from notify_common import group_for_posting, pace, summary_text


# ── Card Building ────────────────────────────────────────────────────────────

def _product_color(product_name: str) -> str:
    """Generate a consistent hex colour from a product name."""
    h = hashlib.md5(product_name.encode()).hexdigest()
    r = 60 + int(h[0:2], 16) % 180
    g = 60 + int(h[2:4], 16) % 180
    b = 60 + int(h[4:6], 16) % 180
    return f"#{r:02x}{g:02x}{b:02x}"


def _build_item_card(item: dict, card_index: int) -> dict:
    """Build a Google Chat Card v2 for a single release-note item."""
    product_name = item.get("product_name", "Unknown")
    icon_url = item.get("icon_url", "")
    title = item.get("title", "No title")
    summary = item.get("summary", "")
    link = item.get("link", "")

    # Card header with product name
    header = {
        "title": product_name,
        "subtitle": "New Release Note",
    }
    if icon_url:
        header["imageUrl"] = icon_url
        header["imageType"] = "CIRCLE"

    # Body widgets
    widgets = []

    # Title widget
    widgets.append({
        "decoratedText": {
            "text": f"<b>{title}</b>",
            "wrapText": True,
        }
    })

    # Summary widget
    if summary:
        truncated = (summary[:400] + "…") if len(summary) > 400 else summary
        widgets.append({
            "textParagraph": {
                "text": truncated,
            }
        })

    # Link button
    if link:
        widgets.append({
            "buttonList": {
                "buttons": [{
                    "text": "View Release Notes",
                    "onClick": {
                        "openLink": {"url": link}
                    }
                }]
            }
        })

    sections = [{"widgets": widgets}]

    return {
        "cardId": f"release-item-{card_index}",
        "card": {
            "header": header,
            "sections": sections,
        }
    }


def _build_footer_card(item_count: int, card_index: int) -> dict:
    """Build a footer card with timestamp."""
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    return {
        "cardId": f"release-footer-{card_index}",
        "card": {
            "sections": [{
                "widgets": [
                    {
                        "decoratedText": {
                            "text": f"<i>Updated {now}</i>",
                            "bottomLabel": f"{item_count} new release note{'s' if item_count != 1 else ''}",
                        }
                    }
                ]
            }]
        }
    }


# ── Sending ──────────────────────────────────────────────────────────────────

def send_gchat_notifications(new_items: list[dict], base_url: str):
    """Send Google Chat webhook notifications for new release notes items."""
    if not new_items:
        return

    # One post per product by default - see notify_common.
    posts = group_for_posting(new_items, lambda item: item.get("gchat_webhook", ""))

    if not posts:
        print("  No Google Chat webhooks configured – skipping notifications")
        return

    for index, (webhook_url, items) in enumerate(posts):
        pace(index)
        try:
            # Build cards — one per item. The footer only earns its place on a
            # mixed batch; on a per-product post it is repeated clutter.
            single_product = len({i.get("product_id") or i.get("product_name") for i in items}) == 1
            cards = []
            for i, item in enumerate(items):
                cards.append(_build_item_card(item, i))
            if not single_product:
                cards.append(_build_footer_card(len(items), len(items)))

            payload = {"cardsV2": cards}

            resp = requests.post(
                webhook_url,
                headers={"Content-Type": "application/json; charset=UTF-8"},
                json=payload,
                timeout=15,
            )

            if resp.status_code == 200:
                masked = webhook_url[:60] + "…" if len(webhook_url) > 60 else webhook_url
                print(f"  Google Chat: posted \"{summary_text(items)}\" to {masked}")
            else:
                print(f"  Google Chat error: {resp.status_code} {resp.text[:500]}")

        except Exception as exc:
            print(f"  Google Chat exception: {exc}")
