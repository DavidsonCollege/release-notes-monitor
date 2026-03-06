"""
Zoom Team Chat notifications for Release Notes Monitor.

Sends formatted messages to per-team Zoom channels.

Supports two modes:

    1. Chatbot API — uses ZOOM_BOT_JID + client credentials to post as a named
       bot with its own icon.  Requires the Zoom app to have the "Team Chat"
       feature enabled and the imchat:bot scope.

    2. User Chat API (fallback) — uses Server-to-Server OAuth to post via
       /chat/users/me/messages.  Messages appear under the service account's
       identity rather than a bot.

Note: Zoom's User Chat API supports only basic markdown (bold, italic,
strikethrough).  It does NOT support inline images, markdown links
[text](url), or horizontal rules (---).  The formatting helpers in this
module account for these limitations.
"""

import os
import json
import base64
from datetime import datetime, timezone

import requests

ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"
ZOOM_CHATBOT_URL = "https://api.zoom.us/v2/im/chat/messages"
ZOOM_CHAT_URL = "https://api.zoom.us/v2/chat/users/me/messages"

# JID suffix for Zoom Team Chat group channels
CHANNEL_JID_SUFFIX = "@conference.xmpp.zoom.us"

# Unicode separator for visual card separation in plain-text messages
SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


# ── Authentication ─────────────────────────────────────────────────────────────

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
    """Obtain a Zoom Chatbot token using client_credentials grant.

    Uses the General App credentials (ZOOM_CHATBOT_CLIENT_ID / SECRET),
    which are separate from the Server-to-Server OAuth credentials used
    by the User Chat API fallback.
    """
    client_id = os.environ.get("ZOOM_CHATBOT_CLIENT_ID", "")
    client_secret = os.environ.get("ZOOM_CHATBOT_CLIENT_SECRET", "")
    if not all([client_id, client_secret]):
        raise RuntimeError("Missing ZOOM_CHATBOT_CLIENT_ID or ZOOM_CHATBOT_CLIENT_SECRET")

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


def _get_admin_user_jid(s2s_token: str) -> str:
    """Fetch a user's JID from the Zoom account for the Chatbot API.

    The Chatbot API requires a real user_jid (not the bot's JID).
    For user-managed apps, this must be the user who installed the app.

    If ZOOM_USER_EMAIL is set, looks up that specific user.
    Otherwise falls back to the first active user (which may not work
    for user-managed apps if the first user isn't the app installer).
    """
    target_email = os.environ.get("ZOOM_USER_EMAIL", "")

    try:
        if target_email:
            # Look up the specific user by email
            resp = requests.get(
                f"https://api.zoom.us/v2/users/{target_email}",
                headers={"Authorization": f"Bearer {s2s_token}"},
                timeout=15,
            )
            resp.raise_for_status()
            user = resp.json()
            user_id = user.get("id", "")
            email = user.get("email", target_email)
            if user_id:
                jid = f"{user_id}@xmpp.zoom.us"
                print(f"    Resolved user_jid: {jid} ({email})")
                return jid
            print(f"    Warning: /v2/users/{target_email} returned no user ID")
        else:
            # Fallback: first active user
            resp = requests.get(
                "https://api.zoom.us/v2/users",
                headers={"Authorization": f"Bearer {s2s_token}"},
                params={"page_size": 1, "status": "active"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            users = data.get("users", [])
            if users:
                user_id = users[0].get("id", "")
                email = users[0].get("email", "?")
                if user_id:
                    jid = f"{user_id}@xmpp.zoom.us"
                    print(f"    Resolved user_jid: {jid} ({email})")
                    return jid
            print(f"    Warning: /v2/users returned no active users")
    except Exception as exc:
        print(f"    Warning: could not fetch user JID: {exc}")
    return ""


# ── Message formatting (User Chat API — plain text with basic markdown) ──

def _build_user_chat_card(item: dict) -> str:
    """Build a plain-text card for one release-note item.

    Uses only formatting that the User Chat API actually supports:
    bold (**text**), plain URLs on their own line, and Unicode characters
    for visual structure.
    """
    product_name = item.get("product_name", "Unknown")
    title = item.get("title", "No title")
    summary = item.get("summary", "")
    link = item.get("link", "")

    lines: list[str] = []

    # Product name (bold)
    lines.append(f"**{product_name}**")

    # Title (bold)
    lines.append(f"**{title}**")

    # Summary
    if summary:
        truncated = (summary[:300] + "…") if len(summary) > 300 else summary
        lines.append(truncated)

    # Link on its own line (plain URL — Zoom auto-links it)
    if link:
        lines.append(link)

    return "\n".join(lines)


def _build_user_chat_message(items: list[dict], base_url: str) -> str:
    """Build a complete message for the User Chat API."""
    cards = []
    for item in items:
        cards.append(_build_user_chat_card(item))

    body = f"\n\n{SEPARATOR}\n\n".join(cards)

    # Footer
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    body += f"\n\n{SEPARATOR}\nUpdated {now}\n{base_url}"

    return body


# ── Message formatting (Chatbot API — message elements with Zoom markdown) ──

def _build_chatbot_body_element(item: dict) -> dict:
    """Build a message body element for a single release-note item.

    Uses the 'message' body type with is_markdown_support for rich formatting.
    Zoom Chatbot markdown links use <url|text> syntax (not [text](url)).

    Note: Zoom does not support inline images in message text, so product
    icons are omitted.  The bot's own avatar serves as the visual identity.
    """
    product_name = item.get("product_name", "Unknown")
    title = item.get("title", "No title")
    summary = item.get("summary", "")
    link = item.get("link", "")

    lines: list[str] = []

    # Product name (bold)
    lines.append(f"*{product_name}*")

    # Title (bold, linked if URL available)
    if link:
        lines.append(f"*<{link}|{title}>*")
    else:
        lines.append(f"*{title}*")

    # Summary
    if summary:
        truncated = (summary[:300] + "…") if len(summary) > 300 else summary
        lines.append(truncated)
        if link:
            lines.append(f"<{link}|View full details →>")
    elif link:
        lines.append(f"<{link}|Details →>")

    text = "\n".join(lines)

    return {
        "type": "message",
        "text": text,
        "is_markdown_support": True,
    }


def _build_chatbot_footer(base_url: str) -> dict:
    """Build a footer message body element with timestamp and link."""
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    return {
        "type": "message",
        "text": f"_{SEPARATOR}_\n_Updated {now} • <{base_url}|Release Notes Monitor>_",
        "is_markdown_support": True,
    }


def _build_chatbot_body(items: list[dict], base_url: str) -> list[dict]:
    """Build the complete body array for the Chatbot API content object."""
    body: list[dict] = []
    for item in items:
        body.append(_build_chatbot_body_element(item))
    body.append(_build_chatbot_footer(base_url))
    return body


# ── Sending ─────────────────────────────────────────────────────────────────

def _to_channel_jid(channel_id: str) -> str:
    """Convert a plain channel ID to a full Zoom JID if needed.

    Zoom's Chatbot API requires to_jid in JID format:
      <channel_id>@conference.xmpp.zoom.us   (for group channels)
    If the channel_id already contains '@', assume it is already a JID.
    """
    if "@" in channel_id:
        return channel_id
    return f"{channel_id}{CHANNEL_JID_SUFFIX}"


def _send_via_chatbot(channel_id: str, body: list[dict], token: str,
                      robot_jid: str, account_id: str,
                      user_jid: str = "") -> bool:
    """Send a message via the Chatbot API (appears as a named bot).

    Args:
        body: Pre-built list of body elements (attachments + footer message).
    """
    to_jid = _to_channel_jid(channel_id)
    payload = {
        "robot_jid": robot_jid,
        "to_jid": to_jid,
        "user_jid": user_jid or robot_jid,
        "account_id": account_id,
        "is_markdown_support": True,
        "content": {
            "head": {
                "text": "Release Notes Monitor",
            },
            "body": body,
        },
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

    print(f"  Zoom chatbot error ({to_jid}): {resp.status_code} {resp.text[:500]}")
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
    to post to. Items without a channel are skipped.

    If ZOOM_BOT_JID is set, messages are sent via the Chatbot API and
    appear under the bot's identity. Otherwise, falls back to the User
    Chat API (messages appear as the service account user).
    """
    has_chatbot_creds = bool(os.environ.get("ZOOM_CHATBOT_CLIENT_ID", ""))
    has_s2s_creds = bool(os.environ.get("ZOOM_CLIENT_ID", ""))

    if not has_chatbot_creds and not has_s2s_creds:
        if new_items:
            print("  No Zoom credentials set – skipping Zoom notifications")
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
            # Fetch a real user JID for the Chatbot API
            admin_jid = ""
            if has_s2s_creds:
                try:
                    s2s_token = _get_access_token()
                    admin_jid = _get_admin_user_jid(s2s_token)
                except Exception:
                    pass
            if admin_jid:
                print(f"  Zoom: using Chatbot API (bot identity, user_jid={admin_jid})")
            else:
                print("  Zoom: using Chatbot API (bot identity, WARNING: no user_jid found, using robot_jid as fallback)")
        else:
            token = _get_access_token()
            print("  Zoom: using User Chat API (service account identity)")
    except Exception as exc:
        print(f"  Zoom OAuth error: {exc}")
        return

    for channel_id, items in by_channel.items():
        try:
            if use_chatbot:
                body = _build_chatbot_body(items, base_url)
                ok = _send_via_chatbot(channel_id, body, token, robot_jid, account_id, admin_jid)
            else:
                message = _build_user_chat_message(items, base_url)
                ok = _send_via_user_chat(channel_id, message, token)

            if ok:
                print(f"  Zoom: posted {len(items)} items to {channel_id}")
        except Exception as exc:
            print(f"  Zoom exception ({channel_id}): {exc}")
