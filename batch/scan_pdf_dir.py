"""Scan a PDF folder and route each PDF: papers to Zotero, personal docs to a local archive.

This is the WorkBuddy-managed equivalent of the plugin's standalone-watcher,
extended with a paper-vs-personal routing layer. It shares the SAME port as the
plugin for papers (source folder + /wechat-zotero/import + SHA256 deduplication),
but diverts non-academic PDFs to a local archive so the Zotero library stays a
clean literature store.

Routing (filename keyword based, deliberately conservative):
  - academic paper  -> /wechat-zotero/import  (plugin classifies into 微信文献)
  - personal doc    -> copied into D:/PaperFlow/个人资料/<YYYY>/

Safety properties:
  - single-instance lock, bounded batch
  - SHA256 dedup for both routes (plugin completedHashes + local archive ledger)
  - never moves/deletes the source PDF; archive is a copy
  - no network beyond the local Zotero bridge (127.0.0.1:23119)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parent
# 以下默认值均为示例占位，实际路径请通过命令行参数或环境变量覆盖：
#   --source  微信文件目录（每台电脑不同）
#   --archive 个人资料归档目录
DEFAULT_SOURCE = os.environ.get("PAPERFLOW_SOURCE", r"D:\WeChat\msg\file")
DEFAULT_ARCHIVE = os.environ.get("PAPERFLOW_ARCHIVE", r"D:\PaperFlow\个人资料")
MAX_DEPTH = 6
BATCH_LIMIT = 50
MAX_PDF = 60 * 1024 * 1024

# Filename keywords that mark a PDF as a personal document, not an academic paper.
# Matched case-insensitively against the filename stem.
PERSONAL_KEYWORDS = (
    "述职", "实习", "证明", "简历", "合同", "发票", "报销", "证件", "证书",
    "成绩单", "通知书", "录取", "协议", "报告书", "工资", "社保", "公积金",
    "护照", "身份证", "户口", "学位", "毕业", "荣誉", "奖状", "申请表",
    "登记表", "审批", "盖章", "签章", "扫描件", "工作", "考勤", "请假",
)


def scan_pdfs(source, max_depth=MAX_DEPTH):
    source = Path(source)
    if not source.is_dir():
        raise RuntimeError("source_dir_missing")
    found = []
    def visit(directory, depth):
        if depth > max_depth:
            return
        try:
            children = list(directory.iterdir())
        except OSError:
            return
        for child in children:
            try:
                if child.is_dir():
                    visit(child, depth + 1)
                elif child.is_file() and child.suffix.lower() == ".pdf":
                    found.append(child)
            except OSError:
                continue
    visit(source, 0)
    return found


def is_personal(filename: str) -> bool:
    stem = Path(filename).stem.lower()
    return any(kw in stem for kw in PERSONAL_KEYWORDS)


def completed_hashes(state_path):
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8-sig"))
        return set(str(h).lower() for h in data.get("completedHashes", {}))
    except (OSError, ValueError):
        return set()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def import_paper(path, digest):
    """Route a paper through the plugin's local import endpoint."""
    import urllib.request
    token_path = Path(os.environ["LOCALAPPDATA"]) / "WeChatZoteroImporter" / "bridge-token"
    token = token_path.read_text(encoding="utf-8-sig").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token):
        raise RuntimeError("bridge_token_invalid")
    payload = {
        "path": str(path),
        "sha256": digest,
        "sourceName": "微信链接整理",
        "extraTags": ["来源/微信文件目录", "触发方式/WorkBuddy定时扫描"],
        "classificationProfileId": "optical_imaging",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-WeChat-Zotero-Token": token,
    }
    request = urllib.request.Request("http://127.0.0.1:23119/wechat-zotero/import",
                                     data=body, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=180) as response:
        return json.loads(response.read(2 * 1024 * 1024 + 1))


def archive_personal(path, digest, archive_root):
    """Copy a personal PDF into D:/PaperFlow/个人资料/<YYYY>/ by file mtime year."""
    archive_root = Path(archive_root)
    year = time.strftime("%Y", time.localtime(path.stat().st_mtime))
    target_dir = archive_root / year
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem[:120]
    target = target_dir / (stem + ".pdf")
    if target.exists():
        if sha256_file(target) == digest:
            return {"status": "archived_duplicate", "path": str(target)}
        # Name collision with different content: disambiguate with a short hash.
        target = target_dir / (stem + "-" + digest[:8] + ".pdf")
    shutil.copy2(path, target)
    return {"status": "archived", "path": str(target)}


def archive_ledger_path(archive_root):
    return Path(archive_root) / ".archive-ledger.json"


def load_archive_ledger(archive_root):
    path = archive_ledger_path(archive_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return set(str(h).lower() for h in data.get("hashes", []))
    except (OSError, ValueError):
        return set()


def save_archive_ledger(archive_root, hashes):
    path = archive_ledger_path(archive_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as out:
        json.dump({"version": 1, "hashes": sorted(hashes)}, out, ensure_ascii=False, indent=2)
    temporary.replace(path)


def run(source, state_path, archive_root, limit=BATCH_LIMIT):
    lock = ROOT / "scan_pdf_dir.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("scan_lock_exists")
    try:
        with os.fdopen(descriptor, "w") as out:
            out.write(str(os.getpid()))
        paper_done = completed_hashes(state_path)
        archive_done = load_archive_ledger(archive_root)
        pdfs = scan_pdfs(source)
        results = {"scanned": len(pdfs), "papers_imported": 0, "papers_duplicate": 0,
                   "personal_archived": 0, "personal_duplicate": 0, "failed": 0,
                   "skipped": 0, "items": []}
        new_archive_hashes = set(archive_done)
        processed = 0
        for path in pdfs:
            if processed >= limit:
                break
            try:
                if path.stat().st_size > MAX_PDF:
                    results["skipped"] += 1
                    continue
                digest = sha256_file(path)
                if is_personal(path.name):
                    if digest in archive_done:
                        results["personal_duplicate"] += 1
                        continue
                    outcome = archive_personal(path, digest, archive_root)
                    processed += 1
                    new_archive_hashes.add(digest)
                    if outcome["status"] == "archived":
                        results["personal_archived"] += 1
                    else:
                        results["personal_duplicate"] += 1
                    results["items"].append({"path": str(path), "digest": digest,
                                             "route": "personal", "status": outcome["status"],
                                             "target": outcome["path"]})
                else:
                    if digest in paper_done:
                        results["papers_duplicate"] += 1
                        continue
                    outcome = import_paper(path, digest)
                    processed += 1
                    status = outcome.get("status")
                    if status in ("imported", "duplicate"):
                        key = "papers_imported" if status == "imported" else "papers_duplicate"
                        results[key] += 1
                    else:
                        results["failed"] += 1
                    results["items"].append({"path": str(path), "digest": digest,
                                             "route": "paper", "status": status,
                                             "itemKey": outcome.get("itemKey")})
            except Exception as error:
                results["failed"] += 1
                results["items"].append({"path": str(path), "error": type(error).__name__})
        save_archive_ledger(archive_root, new_archive_hashes)
        return results
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(DEFAULT_SOURCE))
    parser.add_argument("--state-path", type=Path,
                        default=Path(os.environ.get(
                            "PAPERFLOW_STATE_PATH",
                            r"D:\<Zotero数据目录>\wechat-zotero-importer\standalone-state.json")))
    parser.add_argument("--archive", type=Path, default=Path(DEFAULT_ARCHIVE))
    parser.add_argument("--limit", type=int, default=BATCH_LIMIT)
    args = parser.parse_args()
    try:
        report = run(args.source, args.state_path, args.archive, args.limit)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except RuntimeError as error:
        print("Scan stopped: " + str(error))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
