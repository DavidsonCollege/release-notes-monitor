"""
Slack bot notifications for Release Notes Monitor.
Sends Block Kit formatted messages to per-team Slack channels
using a bot token with chat:write scope.

Each release-note item is rendered as a colored attachment card
with a product icon byline, bold title, and summary or link.
"""
import os
import json
from datetime import datetime, timezone

import requests

SLACK_API_URL = "https://slack.com/api/chat.postMessage"

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

    # Group items by target channel
    by_channel: dict[str, list[dict]] = {}
    for item in new_items:
        channel = item.get("slack_channel", "") or default_channel
        if not channel:
            continue
        by_channel.setdefault(channel, []).append(item)

    if not by_channel:
        print("  No Slack channels configured – skipping notifications")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    for channel, items in by_channel.items():
        attachments = _build_attachments(items, base_url)
        payload = {
            "channel": channel,
            "attachments": attachments,
            "text": f"{len(items)} new release note{'s' if len(items) != 1 else ''}",
        }
        try:
            resp = requests.post(SLACK_API_URL, headers=headers,
                                 json=payload, timeout=10)
            data = resp.json()
            if data.get("ok"):
                print(f"  Slack: posted {len(items)} items to {channel}")
            else:
                print(f"  Slack error ({channel}): {data.get('error', resp.text)}")
        except Exception as exc:
            print(f"  Slack exception ({channel}): {exc}")


def _build_card_blocks(item: dict) -> list[dict]:
    """Build Block Kit blocks for a single release-note item."""
    product_name = item.get("product_name", "Unknown")
    icon_url = item.get("icon_url", "")
    title = item.get("title", "No title")
    summary = item.get("summary", "")
    link = item.get("link", "")

    blocks: list[dict] = []

    # ── Row 1: Product icon + product name (context byline) ──
    context_elements: list[dict] = []
    if icon_url:
        context_elements.append({
            "type": "image",
            "image_url": icon_url,
            "alt_text": product_name,
        })
    context_elements.append({
        "type": "mrkdwn",
        "text": f"*{product_name}*",
    })
    blocks.append({"type": "context", "elements": context_elements})

    # ── Row 2: Title (bold, prominent) ──
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


def _build_attachments(items: list[dict], base_url: str) -> list[dict]:
    """Build a list of Slack attachments — one colored card per item.

    Each attachment gets a colored left border (CARD_COLOR) and contains
    Block Kit blocks for that item.  A final footer attachment shows
    the timestamp and dashboard link.
    """
    attachments: list[dict] = []

    for item in items:
        attachments.append({
            "color": CARD_COLOR,
            "blocks": _build_card_blocks(item),
        })

    # Footer attachment (no color bar)
    attachments.append({
        "color": "#e0e0e0",
        "blocks": [
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Updated {datetime.now(timezone.utc).strftime('%b %d, %Y %H:%M UTC')}"
                            f"  •  <{base_url}|Release Notes Monitor>"
                        ),
                    }
                ],
            }
        ],
    })

    return attachments
