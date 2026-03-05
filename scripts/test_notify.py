#!/usr/bin/env python3
"""
Send test notifications to verify Slack and Zoom channel configuration.

Creates a realistic-looking fake release note card and sends it to all
configured channels, so teams can confirm integrations are working before
real release notes appear.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow imports from this directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from slack_notify import send_slack_notifications
from zoom_notify import send_zoom_notifications

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "teams.json"

# Placeholder bell icon for the test card
TEST_ICON_URL = "https://cdn-icons-png.flaticon.com/512/1827/1827422.png"


def create_test_item(team: dict, base_url: str) -> dict:
    """Create a fake release-note item for a team's configured channels."""
    return {
        "id": f"test-{team['id']}",
        "product_id": "test-notification",
        "product_name": "Test Notification",
        "icon_url": TEST_ICON_URL,
        "title": "If you see this, notifications are working!",
        "link": base_url,
        "summary": (
            "This is a test from Release Notes Monitor. "
            "Real notifications will appear here when product updates are detected."
        ),
        "date": datetime.now(timezone.utc).isoformat(),
        "slack_channel": team.get("slack_channel", ""),
        "zoom_channel": team.get("zoom_channel", ""),
    }


def main():
    print("=" * 60)
    print("  Release Notes Monitor — Test Notification")
    print("=" * 60)

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    teams = config.get("teams", [])
    base_url = os.environ.get(
        "BASE_URL",
        "https://davidsoncollege.github.io/release-notes-monitor/",
    )

    test_items: list[dict] = []
    for team in teams:
        has_slack = bool(team.get("slack_channel"))
        has_zoom = bool(team.get("zoom_channel"))

        if not has_slack and not has_zoom:
            print(f"  ⚠  {team['name']}: no channels configured — skipping")
            continue

        targets = []
        if has_slack:
            targets.append("Slack")
        if has_zoom:
            targets.append("Zoom")

        item = create_test_item(team, base_url)
        test_items.append(item)
        print(f"  ✓  {team['name']}: sending test to {', '.join(targets)}")

    if not test_items:
        print("\nNo teams have notification channels configured. Nothing to send.")
        sys.exit(0)

    print(f"\nSending {len(test_items)} test notification(s)...\n")

    print("--- Slack ---")
    send_slack_notifications(test_items, base_url)

    print("--- Zoom ---")
    send_zoom_notifications(test_items, base_url)

    print("\n" + "=" * 60)
    print("  Done! Check your channels to confirm delivery.")
    print("=" * 60)


if __name__ == "__main__":
    main()
