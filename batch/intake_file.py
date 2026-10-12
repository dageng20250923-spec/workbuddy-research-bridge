"""PaperFlow 文件入口：把 WorkBuddy 微信助理收到的 PDF 收进本地 inbox，再复用目录分流。

分工：
  - `--file/--dir`：把助理会话里收到的 PDF **复制**进 batch/inbox/（不改动、不删除原文件），
    只接受 .pdf、单文件 ≤ 60MB、按 SHA256 去重，同时写 intake 账本便于回溯。
  - 不带收件参数：扫描 batch/inbox/，交给 scan_pdf_dir 的同一条分流逻辑
    —— 论文走 /wechat-zotero/import 进「微信文献」分类，个人资料复制到归档目录。

与 scan_pdf_dir.py 共用同一份 SHA256 状态与归档账本，所以同一个人文件
无论从微信文件目录还是从助理 inbox 进来，都只会被处理一次。

边界（与项目约定一致）：
  - 只复制、不移动/不删除来源；不读微信 UI；除本机 Zotero 桥（127.0.0.1:23119）外不联网。
  - 不做论文身份核验、不下载、不改 Zotero 库结构。

用法：
    python -B batch/intake_file.py --file "D:\\path\\paper.pdf" [--file ...]
    python -B batch/intake_file.py --dir "D:\\path\\somewhere"
    python -B batch/intake_file.py            # 处理 inbox（论文→Zotero，个人资料→归档）
    python -B batch/intake_file.py --list     # 只看 inbox 现状
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import scan_pdf_dir  # noqa: E402  （复用分流/去重/导入逻辑，避免两份实现）

INBOX = ROOT / "inbox"
INTAKE_LEDGER = INBOX / ".intake-ledger.json"
MAX_PDF = scan_pdf_dir.MAX_PDF


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def load_ledger() -> dict:
    try:
        return json.loads(INTAKE_LEDGER.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"version": 1, "files": {}}


def save_ledger(ledger: dict) -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    temporary = INTAKE_LEDGER.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as out:
        json.dump(ledger, out, ensure_ascii=False, indent=2)
    temporary.replace(INTAKE_LEDGER)


def inbox_hashes() -> set[str]:
    return set(load_ledger().get("files", {}))


def intake(paths: list[Path]) -> dict:
    INBOX.mkdir(parents=True, exist_ok=True)
    ledger = load_ledger()
    known = set(ledger.get("files", {}))
    result = {"accepted": [], "already_present": [], "rejected": []}
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            result["rejected"].append({"path": str(raw), "reason": "not_a_file"})
            continue
        if path.suffix.lower() != ".pdf":
            result["rejected"].append({"path": str(path), "reason": "not_pdf"})
            continue
        if path.stat().st_size > MAX_PDF:
            result["rejected"].append({"path": str(path), "reason": "too_large"})
            continue
        digest = sha256_file(path)
        if digest in known:
            result["already_present"].append({"path": str(path), "sha256": digest})
            continue
        target = INBOX / path.name
        if target.exists():
            target = INBOX / f"{path.stem}-{digest[:8]}.pdf"
        shutil.copy2(path, target)          # 复制，来源文件保持不动
        ledger.setdefault("files", {})[digest] = {"inbox_name": target.name, "source": str(path)}
        known.add(digest)
        result["accepted"].append({"path": str(path), "sha256": digest, "inbox": str(target)})
    save_ledger(ledger)
    return result


def list_inbox() -> dict:
    if not INBOX.is_dir():
        return {"inbox": str(INBOX), "exists": False, "files": []}
    files = [p.name for p in sorted(INBOX.iterdir()) if p.is_file() and p.suffix.lower() == ".pdf"]
    return {"inbox": str(INBOX), "exists": True, "files": files}


def main() -> int:
    parser = argparse.ArgumentParser(description="把助理收到的 PDF 收进 inbox 并复用目录分流")
    parser.add_argument("--file", action="append", default=[], type=Path, help="助理收到的文件路径，可重复")
    parser.add_argument("--dir", action="append", default=[], type=Path, help="批量收件目录，可重复")
    parser.add_argument("--state-path", type=Path, default=Path(r"D:\PaperFlow\standalone-state.json"))
    parser.add_argument("--archive", type=Path, default=Path(scan_pdf_dir.DEFAULT_ARCHIVE))
    parser.add_argument("--limit", type=int, default=scan_pdf_dir.BATCH_LIMIT)
    parser.add_argument("--list", action="store_true", help="只列出 inbox 现状")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_inbox(), ensure_ascii=False, indent=2))
        return 0

    collected: list[Path] = list(args.file)
    for directory in args.dir:
        if directory.is_dir():
            collected.extend(scan_pdf_dir.scan_pdfs(directory))
    if collected:
        print(json.dumps({"intake": intake(collected)}, ensure_ascii=False, indent=2))

    if not INBOX.is_dir():
        print(json.dumps({"skipped": "inbox_missing"}, ensure_ascii=False))
        return 0
    try:
        report = scan_pdf_dir.run(INBOX, args.state_path, args.archive, args.limit)
    except RuntimeError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"inbox_scan": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
