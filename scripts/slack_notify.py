"""
Slack bot notifications for Release Notes Monitor.

Sends Block Kit formatted messages to per-team Slack channels using a bot
token with chat:write scope.  Each release-note item is rendered as a
colored attachment card with a linked title and summary.

By default one message is posted per product (see notify_common), with the
product name as the message header rather than a small icon on each card.
"""

import os
import json
import time
from datetime import datetime, timezone

import requests

from notify_common import group_for_posting, pace, summary_text

SLACK_API_URL = "https://slack.com/api/chat.postMessage"

# chat.postMessage answers 429 with Retry-After when a channel is posted to
# too fast. pace() keeps us under the limit in normal use; this is the
# backstop for a run that turns up an unusual number of products.
MAX_RETRIES = 3

# Accent colour for the card's left-side bar (Davidson red)
CARD_COLOR = "#c91230"


def send_slack_notifications(new_items: list[dict], base_url: str):
    """Send Slack Block Kit notifications for new release notes items.

    Each item may include a 'slack_channel' key indicating which channel
    to post to.  Items without a channel are posted to the fallback
    SLACK_DEFAULT_CHANNEL env-var (if set).
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    default_channel = os.environ.get("SLACK_DEFAULT_CHANNEL", "")

    if not token:
        if new_items:
            print("  SLACK_BOT_TOKEN not set – skipping Slack notifications")
        return

    if not new_items:
        return

    posts = group_for_posting(
        new_items,
        lambda item: item.get("slack_channel", "") or default_channel,
    )

    if not posts:
        print("  No Slack channels configured – skipping notifications")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    for index, (channel, items) in enumerate(posts):
        pace(index)
        payload = _build_payload(channel, items, base_url)
        label = summary_text(items)
        try:
            _post(payload, headers, channel, label)
        except Exception as exc:
            print(f"  Slack exception ({channel}): {exc}")


def _post(payload: dict, headers: dict, channel: str, label: str):
    """POST one message, backing off if Slack rate-limits us."""
    for attempt in range(MAX_RETRIES):
        resp = requests.post(SLACK_API_URL, headers=headers, json=payload, timeout=10)

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "1")) or 1
            print(f"  Slack: rate limited on {channel}, waiting {wait}s")
            time.sleep(wait)
            continue

        data = resp.json()
        if data.get("ok"):
            print(f"  Slack: posted \"{label}\" to {channel}")
        else:
            print(f"  Slack error ({channel}): {data.get('error', resp.text)}")
        return

    print(f"  Slack: gave up on \"{label}\" for {channel} after {MAX_RETRIES} attempts")


def _build_payload(channel: str, items: list[dict], base_url: str) -> dict:
    """Build one chat.postMessage payload.

    When every item in the post is the same product, the product name is
    promoted to a header above the cards and dropped from the cards
    themselves - that is the whole point of grouping by product, and
    repeating the name on each card would just add noise.
    """
    products = {i.get("product_id") or i.get("product_name") for i in items}
    single_product = len(products) == 1

    payload = {
        "channel": channel,
        "text": summary_text(items),
        "attachments": _build_attachments(
            items, base_url,
            show_product_header=not single_product,
            show_footer=not single_product,
        ),
    }

    if single_product:
        payload["blocks"] = _build_product_header(items[0])

    return payload


def _build_product_header(item: dict) -> list[dict]:
    """Post-level header: the product name, large, once."""
    product_name = item.get("product_name", "Unknown")
    icon_url = item.get("icon_url", "")

    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": product_name, "emoji": True},
    }]

    elements: list[dict] = []
    if icon_url:
        elements.append({"type": "image", "image_url": icon_url, "alt_text": product_name})
    elements.append({"type": "mrkdwn", "text": "New Release Notes"})
    blocks.append({"type": "context", "elements": elements})

    return blocks


def _build_card_blocks(item: dict, show_product_header: bool = True) -> list[dict]:
    """Build Block Kit blocks for a single release-note item.

    show_product_header is False when the post already names the product in
    its own header (the per-product default).
    """
    product_name = item.get("product_name", "Unknown")
    icon_url = item.get("icon_url", "")
    title = item.get("title", "No title")
    summary = item.get("summary", "")
    link = item.get("link", "")

    blocks: list[dict] = []

    if show_product_header:
        # ── Row 1: Product name (header block = largest text in Block Kit) ──
        blocks.append({
            "type": "header",
            "text": {"type": "plain_text", "text": product_name, "emoji": True},
        })

        # ── Row 2: Product icon (context block, left-aligned) ──
        if icon_url:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "image",
                        "image_url": icon_url,
                        "alt_text": product_name,
                    },
                    {
                        "type": "mrkdwn",
                        "text": "New Release Note",
                    },
                ],
            })

    # ── Row 2: Title (bold, prominent, linked) ──
    if link:
        title_text = f"*<{link}|{title}>*"
    else:
        title_text = f"*{title}*"

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": title_text},
    })

    # ── Row 3: Summary or fallback link ──
    if summary:
        truncated = (summary[:300] + "…") if len(summary) > 300 else summary
        detail_text = truncated
        if link:
            detail_text += f"\n<{link}|View full details →>"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": detail_text},
        })
    elif link:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{link}|Details can be found here →>"},
        })

    return blocks


def _build_attachments(items: list[dict], base_url: str,
                       show_product_header: bool = True,
                       show_footer: bool = True) -> list[dict]:
    """Build a list of Slack attachments — one colored card per item.

    Each attachment gets a colored left border (CARD_COLOR) and contains
    Block Kit blocks for that item.  The footer attachment is skipped for
    per-product posts: it only carried a timestamp, and repeating it on every
    post in a multi-product run is exactly the clutter we are removing.
    """
    attachments: list[dict] = []
    for item in items:
        attachments.append({
            "color": CARD_COLOR,
            "blocks": _build_card_blocks(item, show_product_header=show_product_header),
        })

    if not show_footer:
        return attachments

    # Footer attachment (no color bar)
    attachments.append({
        "color": "#e0e0e0",
        "blocks": [
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Updated {datetime.now(timezone.utc).strftime('%b %d, %Y %H:%M UTC')}",
                    }
                ],
            }
        ],
    })
    return attachments
