"""Shared grouping and pacing for the Slack / Zoom / Google Chat notifiers.

Anne Pender, #dc-product-updates, 2026-08-14:

    "I think it would be easier for me to skim if there was only one product
     per Release Notes Monitor post... Since the product icons are fairly
     small, the post is the easier to see delineator."

Originally every notifier sent exactly one message per destination per run,
with one card per item inside it. The post boundary was therefore "this run",
not "this product" - most runs happen to find one product, which is why it
usually looked right and occasionally didn't.

Modes (set per team as "notification_grouping", or globally with the
NOTIFICATION_GROUPING env var):

    product  one message per product per destination  (default)
    batch    one message per destination per run      (the original)
    item     one message per release note

Keeping this in one place is deliberate: Slack, Zoom Team Chat and Google Chat
are meant to stay 1:1 with each other, and three copies of this logic would
drift.
"""
from __future__ import annotations

import os
import time

DEFAULT_MODE = "product"
VALID_MODES = ("batch", "product", "item")

# Slack's chat.postMessage is roughly one call per second per channel; the
# Zoom and Google Chat endpoints are comparably forgiving. One second between
# posts keeps every platform inside its budget without meaningfully delaying
# a run (the largest batch seen so far is 8 items).
POST_INTERVAL_SECONDS = 1.0


def grouping_mode(item: dict | None = None) -> str:
    """Resolve the grouping mode for an item's team.

    Per-team config wins over the env var, which wins over the default.
    An unrecognised value falls back to the default rather than raising -
    a typo in the dashboard should not stop notifications going out.
    """
    per_team = (item or {}).get("notification_grouping") or ""
    mode = (per_team or os.environ.get("NOTIFICATION_GROUPING", "") or DEFAULT_MODE)
    mode = str(mode).strip().lower()
    if mode not in VALID_MODES:
        print(f"  [WARN] Unknown notification_grouping '{mode}', using '{DEFAULT_MODE}'")
        return DEFAULT_MODE
    return mode


def _product_key(item: dict) -> str:
    return item.get("product_id") or item.get("product_name") or ""


def _product_label(item: dict) -> str:
    return (item.get("product_name") or item.get("product_id") or "").lower()


def group_for_posting(items: list[dict], dest_key) -> list[tuple[str, list[dict]]]:
    """Split items into the messages that should actually be posted.

    Returns a list of (destination, items) pairs. `dest_key` extracts the
    destination (a Slack channel id, Zoom channel id, or Chat webhook URL)
    from an item; items with no destination are dropped.

    Groups come out ordered by product name so a multi-product run reads the
    same way every time; items keep their original order within a group.
    """
    by_dest: dict[str, list[dict]] = {}
    for item in items:
        dest = dest_key(item)
        if not dest:
            continue
        by_dest.setdefault(dest, []).append(item)

    posts: list[tuple[str, list[dict]]] = []
    for dest, dest_items in by_dest.items():
        mode = grouping_mode(dest_items[0])

        if mode == "batch":
            posts.append((dest, dest_items))
            continue

        by_product: dict[str, list[dict]] = {}
        for item in dest_items:
            by_product.setdefault(_product_key(item), []).append(item)

        for key in sorted(by_product, key=lambda k: _product_label(by_product[k][0])):
            group = by_product[key]
            if mode == "item":
                posts.extend((dest, [item]) for item in group)
            else:
                posts.append((dest, group))

    return posts


def summary_text(items: list[dict]) -> str:
    """Notification preview text - what shows in a sidebar or phone banner.

    For a single-product post this leads with the product name, so the reader
    knows what it is without opening the message. That is most of the value of
    the whole change.
    """
    count = len(items)
    plural = "" if count == 1 else "s"
    products = {i.get("product_name") for i in items if i.get("product_name")}
    if len(products) == 1:
        return f"{products.pop()} — {count} new release note{plural}"
    return f"{count} new release note{plural}"


def pace(index: int) -> None:
    """Sleep between posts so a multi-product run does not trip rate limits."""
    if index > 0:
        time.sleep(POST_INTERVAL_SECONDS)
