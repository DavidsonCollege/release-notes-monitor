"""
Slack bot notifications for Release Notes Monitor.
Sends Block Kit formatted messages to per-team Slack channels
using a bot token with chat:write scope.
"""
import os
import json
from datetime import datetime, timezone

import requests

SLACK_API_URL = "https://slack.com/api/chat.postMessage"


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
            print("  SLACK_BOT_TOKEN not set \u2013 skipping Slack notifications")
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
        print("  No Slack channels configured \u2013 skipping notifications")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    for channel, items in by_channel.items():
        blocks = _build_blocks(items, base_url)
        payload = {
            "channel": channel,
            "blocks": blocks,
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


def _build_blocks(items: list[dict], base_url: str) -> list[dict]:
    """Build Block Kit blocks for a list of release-note items."""
    count = len(items)
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"\U0001f4e6 {count} New Release Note{'s' if count != 1 else ''}",
                "emoji": True,
            },
        }
    ]

    # Group by product within the channel
    by_product: dict[str, list[dict]] = {}
    for item in items:
        pname = item.get("product_name", "Unknown")
        by_product.setdefault(pname, []).append(item)

    for product_name, prod_items in by_product.items():
        icon_url = prod_items[0].get("icon_url", "")

        # Product header with icon
        product_block: dict = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{product_name}*"},
        }
        if icon_url:
            product_block["accessory"] = {
                "type": "image",
                "image_url": icon_url,
                "alt_text": product_name,
            }
        blocks.append(product_block)

        # Individual release notes as bullet items
        for item in prod_items:
            title = item.get("title", "No title")
            summary = item.get("summary", "")
            link = item.get("link", "")

            line = f"\u2022  <{link}|{title}>" if link else f"\u2022  {title}"
            if summary:
                truncated = (summary[:150] + "\u2026") if len(summary) > 150 else summary
                line += f"\n     _{truncated}_"

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": line},
            })

        blocks.append({"type": "divider"})

    # Remove trailing divider
    if blocks and blocks[-1].get("type") == "divider":
        blocks.pop()

    # Footer — timestamp only
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Updated {datetime.now(timezone.utc).strftime('%b %d, %Y %H:%M UTC')}",
            }
        ],
    })

    return blocks
