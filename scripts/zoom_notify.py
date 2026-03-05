"""
Zoom Team Chat notifications for Release Notes Monitor.
Sends formatted messages to per-team Zoom channels
using Server-to-Server OAuth with chat_message:write scope.
"""
import os
import json
import base64
from datetime import datetime, timezone

import requests

ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"
ZOOM_CHAT_URL = "https://api.zoom.us/v2/chat/users/me/messages"


def _get_access_token() -> str:
    """Obtain a Zoom Server-to-Server OAuth access token."""
    client_id = os.environ.get("ZOOM_CLIENT_ID", "")
    client_secret = os.environ.get("ZOOM_CLIENT_SECRET", "")
    account_id = os.environ.get("ZOOM_ACCOUNT_ID", "")

    if not all([client_id, client_secret, account_id]):
        raise RuntimeError("Missing ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, or ZOOM_ACCOUNT_ID")

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        ZOOM_OAUTH_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "account_credentials",
            "account_id": account_id,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"No access_token in Zoom OAuth response: {data}")
    return token


def _build_message(items: list[dict], base_url: str) -> str:
    """Build a formatted text message for Zoom Team Chat."""
    count = len(items)
    lines = [f"📣 **{count} New Release Note{'s' if count != 1 else ''}**\n"]

    # Group by product
    by_product: dict[str, list[dict]] = {}
    for item in items:
        pname = item.get("product_name", "Unknown")
        by_product.setdefault(pname, []).append(item)

    for product_name, prod_items in by_product.items():
        for item in prod_items:
            title = item.get("title", "No title")
            summary = item.get("summary", "")
            link = item.get("link", "")

            lines.append(f"**{product_name}**")
            if link:
                lines.append(f"[{title}]({link})")
            else:
                lines.append(title)
            if summary:
                truncated = (summary[:200] + "...") if len(summary) > 200 else summary
                lines.append(truncated)
            lines.append("")  # blank line between items

    # Footer
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    lines.append(f"---\n[View Dashboard]({base_url}) | Updated {now}")

    return "\n".join(lines)


def send_zoom_notifications(new_items: list[dict], base_url: str):
    """Send Zoom Team Chat notifications for new release notes items.

    Each item may include a 'zoom_channel' key indicating which channel
    to post to.  Items without a channel are skipped.
    """
    # Check if Zoom credentials are configured
    if not os.environ.get("ZOOM_CLIENT_ID", ""):
        if new_items:
            print("  ZOOM_CLIENT_ID not set – skipping Zoom notifications")
        return
    if not new_items:
        return

    # Group items by target channel
    by_channel: dict[str, list[dict]] = {}
    for item in new_items:
        channel = item.get("zoom_channel", "")
        if not channel:
            continue
        by_channel.setdefault(channel, []).append(item)

    if not by_channel:
        print("  No Zoom channels configured – skipping notifications")
        return

    # Get OAuth token
    try:
        token = _get_access_token()
    except Exception as exc:
        print(f"  Zoom OAuth error: {exc}")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    for channel_id, items in by_channel.items():
        message = _build_message(items, base_url)
        payload = {
            "message": message,
            "to_channel": channel_id,
        }
        try:
            resp = requests.post(ZOOM_CHAT_URL, headers=headers,
                                 json=payload, timeout=15)
            if resp.status_code in (200, 201):
                print(f"  Zoom: posted {len(items)} items to {channel_id}")
            else:
                print(f"  Zoom error ({channel_id}): {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            print(f"  Zoom exception ({channel_id}): {exc}")
