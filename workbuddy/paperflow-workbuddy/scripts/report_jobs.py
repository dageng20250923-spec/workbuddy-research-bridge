"""Offline, read-only status projection. No network, downloads or application writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

MAX_JSON = 1024 * 1024
MAX_PDF = 60 * 1024 * 1024
DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\Z")
KNOWN_ERRORS = {
    "main_paper_needs_review": "请确认公众号中的主论文 DOI。",
    "ambiguous_main_paper": "存在多篇或身份歧义，请确认主论文。",
    "official_fulltext_not_open": "请提供合法全文访问途径或选择暂缓。",
    "paywall": "请主动提供合法全文访问；不会自动弹出登录窗口。",
    "login_required": "需要用户主动处理合法访问，不会自动登录。",
    "source_policy_unverified": "请先核验 scansci-pdf 合法来源策略。",
    "scansci_unavailable": "请先连接 scansci-pdf 技能及可用工具。",
    "unsupported_url": "该来源尚未接入，请提供明确 DOI。",
    "download_time_limit": "下载超时，请检查网络后主动重试。",
    "tls_or_connection_failed": "网络连接失败，请检查网络后主动重试。",
    "metadata_identity_mismatch": "题名或 DOI 不匹配，请核验论文身份。",
    "pdf_identity_mismatch": "PDF 身份不匹配，不会导入。",
    "artifact_hash_changed": "下载文件校验值变化，请核验文件，不会导入。",
    "processing_failed": "处理中断，请检查诊断；不会显示原始敏感错误。",
}
LIBRARY_COMPLETE = {"imported", "duplicate", "existing_doi"}


def bounded_json(path: Path, root: Path):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path_outside_scope")
    if path.stat().st_size > MAX_JSON:
        raise ValueError("record_too_large")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("record_too_large")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("record_not_object")
    return value


def safe_title(value):
    if not isinstance(value, str):
        return "题名待核验"
    # Only display title text; no raw markup, line breaks or error strings.
    value = " ".join(value.split())[:240]
    return value.replace("<", "‹").replace(">", "›") or "题名待核验"


def iso_time(value):
    if type(value) not in (int, float) or not 0 < value < 4102444800:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def recorded_integrity(job, batch: Path):
    """Check file/hash in the explicit downloads scope; not a new identity/license verifier."""
    digest = job.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return False
    location = job.get("pdf_path")
    if not isinstance(location, str):
        return False
    path = Path(location)
    downloads = batch / "downloads"
    if (downloads.is_symlink() or path.is_symlink()
            or not path.resolve().is_relative_to(downloads.resolve())):
        return False
    try:
        if not 10 < path.stat().st_size <= MAX_PDF:
            return False
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                return False
            stream.seek(0)
            read = 0
            while chunk := stream.read(65536):
                read += len(chunk)
                if read > MAX_PDF:
                    return False
                checksum.update(chunk)
        return checksum.hexdigest() == digest
    except OSError:
        return False


def projection(job, batch: Path):
    task_id = job.get("task_id")
    if type(task_id) is not int or task_id <= 0:
        raise ValueError("invalid_task_id")
    identifier = job.get("doi")
    identifier = identifier if isinstance(identifier, str) and DOI.fullmatch(identifier) else None
    row = {"task_id": task_id, "title": safe_title(job.get("title")), "doi": identifier,
           "public_source": "https://doi.org/" + identifier if identifier else None,
           "last_updated_utc": iso_time(job.get("updated_at")),
           "download": "not_downloaded", "library": "not_started",
           "notes": "not_started", "ai": "not_observed", "reason": None,
           "advice": None}
    state = job.get("status")
    if state == "downloaded":
        if not identifier or not recorded_integrity(job, batch):
            row.update(download="needs_review", reason="artifact_integrity_failed",
                       advice="文件、DOI记录或校验值异常；停止导入并核验，不会自动重下载。")
            return row
        row["download"] = "recorded_verified"
    else:
        error = job.get("error")
        reason = error if isinstance(error, str) and error in KNOWN_ERRORS else None
        if reason is None and not error:
            reason = {"pending": "queued", "resolved": "resolved_waiting_download"}.get(state)
        row["reason"] = reason or "unclassified_failure"
        row["advice"] = KNOWN_ERRORS.get(row["reason"], "请检查任务配置或诊断；该篇尚未确认下载。")
        return row
    library = job.get("library")
    library = library if isinstance(library, dict) else {}
    status = library.get("status")
    if status in LIBRARY_COMPLETE:
        row["library"] = "ready"
    elif status == "import_in_flight":
        row["library"] = "reconcile_required"
        row["advice"] = "导入结果需对账，不能盲目重复导入。"
    else:
        row["library"] = "pending" if not status else "needs_review"
    notes = job.get("notes")
    notes = notes if isinstance(notes, dict) else {}
    if row["library"] == "ready":
        row["notes"] = "recorded_ready" if notes.get("status") in ("existing_note", "note_ready") else "pending"
    report_path = batch / "reports" / (str(task_id) + ".json")
    # Existing observer uses handoff-N.json. No Research Bridge state/config access.
    report_path = report_path.with_name("handoff-" + str(task_id) + ".json")
    if report_path.exists():
        try:
            if ((batch / "reports").is_symlink()
                    or not (batch / "reports").resolve().is_relative_to(batch)):
                raise ValueError("observation_outside_scope")
            report = bounded_json(report_path, batch / "reports")
            if report.get("task_id") != task_id or report.get("doi") != identifier:
                raise ValueError("observation_identity_mismatch")
            if report.get("research_bridge_issue"):
                row["ai"] = "paused_or_needs_review"
                row["advice"] = "该篇已下载；Research Bridge 阶段暂停，请主动检查输入/模型/额度，不会自动付费重试。"
            elif isinstance(report.get("ai_jobs"), list) and report["ai_jobs"]:
                row["ai"] = "job_observed_result_unverified"
        except (OSError, ValueError, TypeError, RecursionError):
            row["ai"] = "observation_needs_review"
    return row


def summarize(batch_dir, limit=200):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("invalid_limit")
    batch = Path(batch_dir).resolve()
    jobs_dir = batch / "jobs"
    result = {"scope": "local_batch_ledger_only", "identity_verification": "prior_download_gate_not_repeated",
              "application_configuration_changed": False, "notification_sent": False,
              "items": [], "issues": [], "not_examined": 0}
    if not jobs_dir.is_dir() or jobs_dir.is_symlink():
        result["issues"].append("ledger_not_configured")
        candidates = []
    else:
        candidates = []
        invalid_names = False
        # Bound both scanning and reading; the unexamined count is not a server queue count.
        for entry_count, path in enumerate(jobs_dir.iterdir()):
            if entry_count >= 1000:
                result["not_examined"] = "unknown_at_least_one"
                result["issues"].append("scan_limit_reached")
                break
            if path.suffix != ".json":
                continue
            if not re.fullmatch(r"[1-9][0-9]{0,17}\.json", path.name):
                invalid_names = True
                continue
            if len(candidates) >= limit:
                result["not_examined"] = "unknown_at_least_one"
                result["issues"].append("scan_limit_reached")
                break
            candidates.append(path)
        if invalid_names:
            result["issues"].append("invalid_ledger_filename")
    for path in sorted(candidates, key=lambda p: int(p.stem)):
        try:
            job = bounded_json(path, jobs_dir)
            if job.get("task_id") != int(path.stem):
                raise ValueError("filename_task_mismatch")
            result["items"].append(projection(job, batch))
        except (OSError, ValueError, TypeError, RecursionError):
            result["issues"].append("ledger_record_unreadable:" + path.stem)
    rows = result["items"]
    result["counts"] = {
        "examined": len(rows),
        "download_complete": sum(r["download"] == "recorded_verified" for r in rows),
        "undownloaded_or_unverified": sum(r["download"] != "recorded_verified" for r in rows),
        "library_ready": sum(r["library"] == "ready" for r in rows),
        "notes_recorded_ready": sum(r["notes"] == "recorded_ready" for r in rows),
        "ai_paused_or_needs_review": sum(r["ai"] in ("paused_or_needs_review", "observation_needs_review") for r in rows),
        "ledger_issues": len(result["issues"]),
    }
    result["undownloaded"] = [r for r in rows if r["download"] != "recorded_verified"]
    semantic = [{k: v for k, v in row.items() if k != "last_updated_utc"} for row in rows]
    result["notification_fingerprint"] = hashlib.sha256(json.dumps(
        {"items": semantic, "issues": result["issues"], "not_examined": result["not_examined"]},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    try:
        result = summarize(args.batch_dir, args.limit)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"issues": ["report_unavailable"], "notification_sent": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
