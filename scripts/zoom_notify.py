"""
Zoom Team Chat notifications for Release Notes Monitor.
Sends formatted messages to per-team Zoom channels.

Supports two modes:
  1. Chatbot API  — uses ZOOM_BOT_JID + client credentials to post as a named
     bot with its own icon.  Requires the Zoom app to have the "Team Chat"
     feature enabled and the imchat:bot scope.
  2. User Chat API (fallback) — uses Server-to-Server OAuth to post via
     /chat/users/me/messages.  Messages appear under the service account's
     identity rather than a bot.
"""
import os
import json
import base64
from datetime import datetime, timezone

import requests

ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"
ZOOM_CHATBOT_URL = "https://api.zoom.us/v2/im/chat/messages"
ZOOM_CHAT_URL = "https://api.zoom.us/v2/chat/users/me/messages"


# ── Authentication ────────────────────────────────────────────────────

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


def _get_chatbot_token() -> str:
    """Obtain a Zoom Chatbot token using client_credentials grant."""
    client_id = os.environ.get("ZOOM_CLIENT_ID", "")
    client_secret = os.environ.get("ZOOM_CLIENT_SECRET", "")

    if not all([client_id, client_secret]):
        raise RuntimeError("Missing ZOOM_CLIENT_ID or ZOOM_CLIENT_SECRET")

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        ZOOM_OAUTH_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"No access_token in Zoom chatbot token response: {data}")
    return token


# ── Message formatting ────────────────────────────────────────────────

def _build_card(item: dict, base_url: str) -> str:
    """Build a single card-style message for one release-note item."""
    product_name = item.get("product_name", "Unknown")
    title = item.get("title", "No title")
    summary = item.get("summary", "")
    link = item.get("link", "")

    lines: list[str] = []

    # Row 1: Product name
    lines.append(f"**{product_name}**")

    # Row 2: Title (bold, linked)
    if link:
        lines.append(f"**[{title}]({link})**")
    else:
        lines.append(f"**{title}**")

    # Row 3: Summary or fallback link
    if summary:
        truncated = (summary[:300] + "…") if len(summary) > 300 else summary
        lines.append(truncated)
        if link:
            lines.append(f"[View full details →]({link})")
    elif link:
        lines.append(f"[Details can be found here →]({link})")

    return "\n".join(lines)


def _build_message(items: list[dict], base_url: str) -> str:
    """Build a formatted text message for Zoom Team Chat."""
    cards = []
    for item in items:
        cards.append(_build_card(item, base_url))

    body = "\n\n---\n\n".join(cards)

    # Footer
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    body += f"\n\n---\n_Updated {now}  •  [Release Notes Monitor]({base_url})_"

    return body


# ── Sending ───────────────────────────────────────────────────────────

def _send_via_chatbot(channel_id: str, message: str, token: str,
                      robot_jid: str, account_id: str) -> bool:
    """Send a message via the Chatbot API (appears as a named bot)."""
    payload = {
        "robot_jid": robot_jid,
        "to_jid": channel_id,
        "account_id": account_id,
        "content": {
            "head": {
                "text": "Release Notes Monitor",
            },
            "body": [
                {
                    "type": "message",
                    "text": message,
                    "is_markdown_support": True,
                }
            ],
        },
        "is_markdown_support": True,
    }
    resp = requests.post(
        ZOOM_CHATBOT_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return True
    print(f"  Zoom chatbot error ({channel_id}): {resp.status_code} {resp.text[:200]}")
    return False


def _send_via_user_chat(channel_id: str, message: str, token: str) -> bool:
    """Send a message via the User Chat API (appears as the service account)."""
    payload = {
        "message": message,
        "to_channel": channel_id,
    }
    resp = requests.post(
        ZOOM_CHAT_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return True
    print(f"  Zoom user-chat error ({channel_id}): {resp.status_code} {resp.text[:200]}")
    return False


def send_zoom_notifications(new_items: list[dict], base_url: str):
    """Send Zoom Team Chat notifications for new release notes items.

    Each item may include a 'zoom_channel' key indicating which channel
    to post to.  Items without a channel are skipped.

    If ZOOM_BOT_JID is set, messages are sent via the Chatbot API and
    appear under the bot's identity.  Otherwise, falls back to the User
    Chat API (messages appear as the service account user).
    """
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

    # Decide which API to use
    robot_jid = os.environ.get("ZOOM_BOT_JID", "")
    account_id = os.environ.get("ZOOM_ACCOUNT_ID", "")
    use_chatbot = bool(robot_jid)

    try:
        if use_chatbot:
            token = _get_chatbot_token()
            print("  Zoom: using Chatbot API (bot identity)")
        else:
            token = _get_access_token()
            print("  Zoom: using User Chat API (service account identity)")
    except Exception as exc:
        print(f"  Zoom OAuth error: {exc}")
        return

    for channel_id, items in by_channel.items():
        message = _build_message(items, base_url)
        try:
            if use_chatbot:
                ok = _send_via_chatbot(channel_id, message, token,
                                       robot_jid, account_id)
            else:
                ok = _send_via_user_chat(channel_id, message, token)
            if ok:
                print(f"  Zoom: posted {len(items)} items to {channel_id}")
        except Exception as exc:
            print(f"  Zoom exception ({channel_id}): {exc}")
