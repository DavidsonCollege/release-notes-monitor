"""
Slack webhook notifications for Release Notes Monitor.
Sends Block Kit formatted messages when new release notes are detected.
"""
import os
import json
from datetime import datetime, timezone

import requests


def send_slack_notifications(new_items: list[dict], base_url: str):
    """Send Slack Block Kit notifications for new release notes items."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url or not new_items:
        if not webhook_url and new_items:
            print("  SLACK_WEBHOOK_URL not set - skipping Slack notifications")
        return

    # Group items by product
    by_product = {}
    for item in new_items:
        pname = item.get("product_name", "Unknown")
        if pname not in by_product:
            by_product[pname] = []
        by_product[pname].append(item)

    # Build Block Kit blocks
    count = len(new_items)
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"\ud83d\udce3 {count} New Release Note{'s' if count != 1 else ''}",
                "emoji": True
            }
        }
    ]

    for product_name, items in by_product.items():
        icon_url = items[0].get("icon_url", "")
        for item in items:
            title = item.get("title", "No title")
            summary = item.get("summary", "")
            link = item.get("link", "")

            text = f"*{product_name}*\n<{link}|{title}>"
            if summary:
                truncated = summary[:200] + ("..." if len(summary) > 200 else "")
                text += f"\n{truncated}"

            block = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            }
            if icon_url:
                block["accessory"] = {
                    "type": "image",
                    "image_url": icon_url,
                    "alt_text": product_name
                }
            blocks.append(block)
            blocks.append({"type": "divider"})

    # Remove trailing divider
    if blocks and blocks[-1].get("type") == "divider":
        blocks.pop()

    # Add footer with link to dashboard
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"<{base_url}|View Dashboard> | Updated {datetime.now(timezone.utc).strftime('%b %d, %Y %H:%M UTC')}"
            }
        ]
    })

    payload = {"blocks": blocks}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"  Slack notification sent ({count} items)")
        else:
            print(f"  Slack notification failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"  Slack notification error: {e}")
