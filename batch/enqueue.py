"""PaperFlow 入队工具：把微信入口收到的候选写入接收队列。

可选入口：用户先配置 WorkBuddy 微信助理，消息到达自己的助手会话后，
由助手调用本脚本把 DOI / 公众号链接写进待处理队列，
之后统一交给 orchestrate.py 做「解析 → scansci-pdf 下载 → Zotero 入库 → Obsidian 观察」。

边界（与项目既有约定一致）：
- 只向 receiver/runtime/queue.sqlite3 的 tasks 表追加 pending 候选；
- 不解析、不下载、不导入、不修改已有任务状态、不触发网络；
- 去重与 receiver 相同：UNIQUE(account_key, kind, value) + INSERT OR IGNORE。

用法：
    python -B batch/enqueue.py --doi 10.1038/s41586-026-10891-z
    python -B batch/enqueue.py --url https://mp.weixin.qq.com/s/xxxxxxxx
    python -B batch/enqueue.py --text "整段消息，自动挑出 DOI / 公众号链接"
    python -B batch/enqueue.py --list
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "receiver" / "runtime" / "queue.sqlite3"

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
DOI_URL_RE = re.compile(r"https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
WECHAT_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s/[\w\-]+")


class Problem(Exception):
    pass


def pick_account_key(override: str | None) -> str:
    """沿用队列里已有的绑定账号；不把该值写进源码，也不打印出来。"""
    if override:
        return override
    with sqlite3.connect(f"{QUEUE.as_uri()}?mode=ro", uri=True) as db:
        keys = [row[0] for row in db.execute("SELECT DISTINCT account_key FROM tasks")]
    if len(keys) == 1:
        return keys[0]
    if not keys:
        raise Problem("queue_has_no_bound_account_use_--account-key")
    raise Problem("multiple_accounts_use_--account-key")


def candidates_from(text: str) -> list[tuple[str, str]]:
    """(kind, value)。doi.org 链接归一到 DOI；公众号链接保持原样。"""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in DOI_URL_RE.findall(text):
        item = ("doi", raw.lower())
        if item not in seen:
            seen.add(item)
            found.append(item)
    for raw in DOI_RE.findall(text):
        item = ("doi", raw.rstrip(".,;)").lower())
        if item not in seen:
            seen.add(item)
            found.append(item)
    for raw in WECHAT_RE.findall(text):
        item = ("article_url", raw)
        if item not in seen:
            seen.add(item)
            found.append(item)
    return found


def enqueue(items: list[tuple[str, str]], account_key: str) -> dict:
    if not QUEUE.is_file():
        raise Problem("queue_missing")
    added, skipped = [], []
    db = sqlite3.connect(QUEUE, timeout=10)
    try:
        for kind, value in items:
            cursor = db.execute(
                "INSERT OR IGNORE INTO tasks(account_key, kind, value, original_value, status, added_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?)",
                (account_key, kind, value, value, int(time.time())),
            )
            (added if cursor.rowcount else skipped).append({"kind": kind, "value": value})
        db.commit()
    finally:
        db.close()
    return {"added": added, "already_queued": skipped}


def list_queue() -> list[dict]:
    with sqlite3.connect(f"{QUEUE.as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT id, kind, value, status, added_at FROM tasks ORDER BY id")]


def main() -> int:
    parser = argparse.ArgumentParser(description="把微信入口收到的 DOI/链接写入待处理队列")
    parser.add_argument("--doi", action="append", default=[], help="DOI，可重复")
    parser.add_argument("--url", action="append", default=[], help="公众号文章链接，可重复")
    parser.add_argument("--text", action="append", default=[], help="整段消息，自动提取候选")
    parser.add_argument("--account-key", default=None, help="覆盖绑定的账号键（默认沿用队列已绑定账号）")
    parser.add_argument("--list", action="store_true", help="只列出当前队列")
    args = parser.parse_args()

    try:
        if args.list:
            print(json.dumps(list_queue(), ensure_ascii=False, indent=2))
            return 0
        items: list[tuple[str, str]] = []
        for text in [*args.doi, *args.url, *args.text]:
            items.extend(candidates_from(text))
        if not items:
            raise Problem("no_doi_or_article_url_found")
        result = enqueue(items, pick_account_key(args.account_key))
        result["queue"] = list_queue()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Problem as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"error": "queue_access_failed"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
