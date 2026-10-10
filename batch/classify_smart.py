"""Agent-assisted dynamic multi-dimension classification for Zotero items.

This is the "smart" replacement for the plugin's single-dimension keyword
classifier. It works in two parts that run at different times:

  1. `evidence`  -- (offline, in automation) dump classification evidence
                   (title / abstract) for items currently in the "待识别"
                   collection, so an agent can read it and decide.
  2. `apply`     -- send the agent's decision to the plugin's NEW
                   `/wechat-zotero/classify` endpoint (use bundled plugin 0.7.4),
                   which creates the collection tree 微信文献 -> <维度> -> <类别>
                   and moves the item there. The dimension is FREE-FORM.

The actual collection creation / item move is performed INSIDE the plugin
(bootstrap.js), which has full access to the Zotero object model — this script
only forwards the agent's decision over HTTP, mirroring how import works.

Fallback contract: if the agent cannot decide, it simply does NOT call `apply`;
the item keeps the plugin's "待识别" assignment. Nothing is ever guessed.

Access is via the local Zotero API (127.0.0.1:23119) with the local bridge token.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import urllib.request

BASE = "http://127.0.0.1:23119"
CLASSIFY_PATH = "/wechat-zotero/classify"
# Replace this placeholder with your own library's "待识别" collection key.
# The publisher's personal collection identifier is deliberately not included.
PENDING_COLLECTION = "YOURKEY1"


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(route):
    req = urllib.request.Request(
        BASE + route, headers={"Zotero-API-Version": "3", "Accept": "application/json"}
    )
    with _opener().open(req, timeout=30) as resp:
        return json.loads(resp.read())


def token():
    path = Path(os.environ["LOCALAPPDATA"]) / "WeChatZoteroImporter" / "bridge-token"
    return path.read_text(encoding="utf-8-sig").strip()


def get_children_text(item_key):
    row = get(f"/api/users/0/items/{item_key}?format=json")
    data = row["data"]
    return data.get("title", ""), data.get("abstractNote", "")


def pending_items():
    rows = get(f"/api/users/0/collections/{PENDING_COLLECTION}/items?format=json&limit=100")
    return [r for r in rows if r["data"].get("itemType") != "attachment"]


def cmd_evidence(_args):
    out = []
    for row in pending_items():
        title, abstract = get_children_text(row["key"])
        out.append({"itemKey": row["key"], "title": title, "abstract": abstract})
    print(json.dumps({"pending_count": len(out), "items": out}, ensure_ascii=False, indent=2))


def cmd_apply(args):
    item_key = args.item_key.strip()
    dimension = args.dimension.strip()
    category = args.category.strip()
    if not re.fullmatch(r"[A-Z0-9]{8}", item_key):
        print("Apply stopped: invalid_item_key")
        raise SystemExit(2)
    if not dimension or not category:
        print("Apply stopped: dimension_and_category_required")
        raise SystemExit(2)

    payload = {"itemKey": item_key, "dimension": dimension, "category": category}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + CLASSIFY_PATH, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-WeChat-Zotero-Token": token(),
            "Accept": "application/json",
        },
    )
    with _opener().open(req, timeout=60) as resp:
        result = json.loads(resp.read())
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("evidence", help="Dump evidence for pending items.")
    apply_p = sub.add_parser("apply", help="Send agent decision to the plugin classify endpoint.")
    apply_p.add_argument("--item-key", required=True)
    apply_p.add_argument("--dimension", required=True, help="Free-form dimension, e.g. 成像方法")
    apply_p.add_argument("--category", required=True, help="Free-form category, e.g. 斜平面显微")
    args = parser.parse_args()
    if args.command == "evidence":
        cmd_evidence(args)
    elif args.command == "apply":
        cmd_apply(args)


if __name__ == "__main__":
    main()
