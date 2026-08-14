#!/usr/bin/env python3
"""
Send test notifications to verify Slack and Zoom channel configuration.

Creates realistic-looking fake release notes and sends them to all configured
channels, so teams can confirm integrations are working before real release
notes appear.

The batch deliberately spans three fake products, one of which has two notes.
A single-item test cannot exercise the multi-product grouping that
notify_common does, and the mixed batch is exactly the case that used to read
badly. Set TEST_SLACK_CHANNEL / TEST_ZOOM_CHANNEL / TEST_GCHAT_WEBHOOK to
redirect a test run at a scratch channel instead of the team's real one.
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
from gchat_notify import send_gchat_notifications

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "teams.json"

# Placeholder bell icon for the test card
TEST_ICON_URL = "https://raw.githubusercontent.com/DavidsonCollege/ti-assets/main/release-notes-monitor-icon-square.png"


# Three fake products, one with two notes - enough to exercise per-product
# grouping and prove the posts split where they should.
TEST_PRODUCTS = [
    ("test-alpha", "Test Product Alpha", [
        "If you see this, notifications are working!",
        "This second note should share a post with the one above.",
    ]),
    ("test-beta", "Test Product Beta", [
        "This note should arrive in its own separate post.",
    ]),
    ("test-gamma", "Test Product Gamma", [
        "And this one in a third post.",
    ]),
]


def create_test_items(team: dict, base_url: str) -> list[dict]:
    """Create a multi-product batch of fake release notes for a team."""
    slack = os.environ.get("TEST_SLACK_CHANNEL", "") or team.get("slack_channel", "")
    zoom = os.environ.get("TEST_ZOOM_CHANNEL", "") or team.get("zoom_channel", "")
    gchat = os.environ.get("TEST_GCHAT_WEBHOOK", "") or team.get("gchat_webhook", "")

    items: list[dict] = []
    for product_id, product_name, titles in TEST_PRODUCTS:
        for index, title in enumerate(titles):
            items.append({
                "id": f"test-{team['id']}-{product_id}-{index}",
                "product_id": product_id,
                "product_name": product_name,
                "icon_url": TEST_ICON_URL,
                "title": title,
                "link": base_url,
                "summary": (
                    "This is a test from Release Notes Monitor. Real notifications "
                    "will appear here when product updates are detected."
                ),
                "date": datetime.now(timezone.utc).isoformat(),
                "slack_channel": slack,
                "zoom_channel": zoom,
                "gchat_webhook": gchat,
                "notification_grouping": team.get("notification_grouping", ""),
            })
    return items


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

    # Optional filter: TEST_TEAMS env var or --teams CLI arg (comma-separated team IDs)
    filter_raw = os.environ.get("TEST_TEAMS", "")
    if not filter_raw and len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg.startswith("--teams="):
                filter_raw = arg.split("=", 1)[1]
    team_filter: set[str] = set()
    if filter_raw:
        team_filter = {t.strip().lower() for t in filter_raw.split(",") if t.strip()}
        print(f"  Filter: sending only to team(s): {', '.join(sorted(team_filter))}\n")

    test_items: list[dict] = []
    for team in teams:
        # Apply team filter if specified
        if team_filter and team["id"].lower() not in team_filter:
            continue

        has_slack = bool(os.environ.get("TEST_SLACK_CHANNEL") or team.get("slack_channel"))
        has_zoom = bool(os.environ.get("TEST_ZOOM_CHANNEL") or team.get("zoom_channel"))
        has_gchat = bool(os.environ.get("TEST_GCHAT_WEBHOOK") or team.get("gchat_webhook"))

        if not has_slack and not has_zoom and not has_gchat:
            print(f"  ⚠  {team['name']}: no channels configured — skipping")
            continue

        targets = []
        if has_slack:
            targets.append("Slack")
        if has_zoom:
            targets.append("Zoom")
        if has_gchat:
            targets.append("Google Chat")

        items = create_test_items(team, base_url)
        test_items.extend(items)
        print(f"  ✓  {team['name']}: sending {len(items)} test notes "
              f"across {len(TEST_PRODUCTS)} products to {', '.join(targets)}")

    if not test_items:
        print("\nNo teams have notification channels configured. Nothing to send.")
        sys.exit(0)

    redirect = os.environ.get("TEST_SLACK_CHANNEL", "")
    if redirect:
        print(f"\n  Slack output redirected to {redirect}")
    print(f"\nSending {len(test_items)} test notification(s)...\n")

    print("--- Slack ---")
    send_slack_notifications(test_items, base_url)

    print("--- Zoom ---")
    send_zoom_notifications(test_items, base_url)

    print("--- Google Chat ---")
    send_gchat_notifications(test_items, base_url)

    print("\n" + "=" * 60)
    print("  Done! Check your channels to confirm delivery.")
    print("=" * 60)


if __name__ == "__main__":
    main()
