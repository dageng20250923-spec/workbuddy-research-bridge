"""End-to-end orchestration: claim -> resolve -> scansci download -> import -> observe.

This is the controlled pipeline that joins the existing, individually-tested
stages under one bounded, single-instance runner. It follows the project rule
that all NEW downloads go through the scansci-pdf suite (via scansci_adapter),
never the legacy in-process network downloader in paper_batch.process().

Safety properties preserved from the underlying stages:
  - single-instance lock (orchestrator.lock), bounded batch, download concurrency 1
  - read-only claim of receiver queue (pending candidates are never mutated here)
  - per-article failure isolation: one failure does not stop the others
  - failures stay in the ledger; nothing is silently marked successful
  - no retry loop, no schedule registration, no WeChat UI, no notification push
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import quote

from paper_batch import (ROOT, DOI_RE, Problem, fetch, article_doi, verify_metadata,
                         atomic_json, LIMIT_HTML)

# The scansci suite's runtime Python must be supplied explicitly by the operator.
# It must live on D: and bundle certifi (scansci_adapter enforces both). This
# project never hardcodes a developer's personal path into shared source.
BATCH_LIMIT = 5


def _queue_path():
    return ROOT.parent / "receiver" / "runtime" / "queue.sqlite3"


def claim_pending(limit=BATCH_LIMIT):
    """Read-only view of pending candidates. Never updates the receiver queue."""
    queue = _queue_path()
    if not queue.is_file():
        raise Problem("queue_missing")
    with sqlite3.connect(queue.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, kind, value FROM tasks WHERE status=? ORDER BY id LIMIT ?",
            ("pending", limit),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_only(task, reviewed=None):
    """Resolve a queue candidate into a resolved job (DOI/title/year) without downloading.

    Reuses paper_batch's narrow metadata/identity checks. The download stage is
    intentionally separate and delegated to scansci_adapter (scansci suite).
    """
    job_path = ROOT / "jobs" / (str(task["id"]) + ".json")
    source = task["value"]
    source_hash = __import__("hashlib").sha256(source.encode()).hexdigest()
    if job_path.exists():
        old = json.loads(job_path.read_text(encoding="utf-8"))
        if old.get("source_hash") == source_hash and old.get("status") in ("resolved", "downloaded"):
            return old
    job = {"task_id": task["id"], "source": source, "source_hash": source_hash,
           "status": "pending", "updated_at": int(time.time()), "profile": "optical_imaging"}
    try:
        evidence = (reviewed or {}).get(source_hash, {})
        if task["kind"] == "doi":
            doi = source.lower()
        elif evidence.get("source") == source and evidence.get("method") == "codex_public_html_review":
            doi = evidence["doi"]
            expected = "https://www.nature.com/articles/" + doi.split("/", 1)[1]
            if evidence.get("primary_url") != expected:
                raise Problem("invalid_review_evidence")
            job["resolution_method"] = "codex_public_html_review"
            job["primary_url"] = expected
        else:
            doi = article_doi(fetch(source, LIMIT_HTML)[0].decode("utf-8"))
        if not re.fullmatch(DOI_RE, doi):
            raise Problem("unsupported_doi")
        raw, mime, _ = fetch("https://api.crossref.org/works/" + quote(doi, safe=""), LIMIT_HTML)
        if mime != "application/json":
            raise Problem("not_metadata_json")
        message = json.loads(raw)["message"]
        job.update(verify_metadata(message, doi))
        job["status"] = "resolved"
    except Problem as error:
        job["error"] = str(error)
    except (OSError, ValueError, KeyError, TypeError, IndexError, ImportError):
        job["error"] = "processing_failed"
    job["updated_at"] = int(time.time())
    atomic_json(job_path, job)
    return job


def stage_download(task_id, scansci_python):
    """Delegate the download to the scansci suite via the isolated adapter."""
    import subprocess
    adapter = ROOT / "scansci_adapter.py"
    result = subprocess.run(
        [sys.executable, "-B", str(adapter), "--task-id", str(task_id),
         "--installed-python", str(scansci_python)],
        cwd=ROOT, capture_output=True, timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def stage_import(task_id):
    """Import a verified PDF into Zotero via the local authorized interface."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "library_handoff.py"), "--task-id", str(task_id)],
        cwd=ROOT, capture_output=True, timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def stage_observe(task_id):
    """Read-only Research Bridge observation; never writes the vault."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "observe_handoff.py"), "--task-id", str(task_id)],
        cwd=ROOT, capture_output=True, timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def load_job(task_id):
    return json.loads((ROOT / "jobs" / (str(task_id) + ".json")).read_text(encoding="utf-8"))


def is_terminal(job):
    """A job whose whole chain already finished; skip without re-running stages."""
    library = job.get("library", {}).get("status")
    notes = job.get("notes", {}).get("status")
    return library in ("imported", "existing_doi", "duplicate") and notes in ("existing_note", "note_ready")


def run(scansci_python, reviewed_sources=False):
    lock = ROOT / "orchestrator.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Problem("orchestrator_lock_exists")
    try:
        with os.fdopen(descriptor, "w") as out:
            out.write(str(os.getpid()))
        reviewed = json.loads((ROOT / "reviewed-sources.json").read_text(encoding="utf-8")) if reviewed_sources else {}
        candidates = claim_pending()
        results = []
        for candidate in candidates:
            task_id = candidate["id"]
            entry = {"task_id": task_id, "source": candidate["value"],
                     "download": "not_attempted", "library": "not_attempted", "notes": "not_attempted"}
            try:
                job = resolve_only(candidate, reviewed=reviewed)
                if is_terminal(job):
                    entry["download"] = "already_complete"
                    entry["library"] = "already_complete"
                    entry["notes"] = "already_complete"
                    entry["job_status"] = job.get("status")
                    entry["library_status"] = job.get("library", {}).get("status")
                    entry["note_status"] = job.get("notes", {}).get("status")
                    results.append(entry)
                    continue
                if job.get("status") != "resolved":
                    entry["download"] = "blocked_resolve"
                    entry["reason"] = job.get("error")
                    results.append(entry)
                    continue
                if job.get("status") == "downloaded" or (job.get("sha256") and (ROOT / "downloads" / (job["sha256"] + ".pdf")).is_file()):
                    entry["download"] = "already_downloaded"
                else:
                    entry["download"] = "downloaded" if stage_download(task_id, scansci_python) else "download_failed"
                if entry["download"] in ("downloaded", "already_downloaded"):
                    entry["library"] = "imported" if stage_import(task_id) else "import_pending"
                if entry["library"] == "imported":
                    entry["notes"] = "observed" if stage_observe(task_id) else "note_pending"
                # Reflect the final durable job state back into the report.
                final = load_job(task_id)
                entry["job_status"] = final.get("status")
                entry["library_status"] = final.get("library", {}).get("status")
                entry["note_status"] = final.get("notes", {}).get("status")
            except Problem as error:
                entry["download"] = "blocked"
                entry["reason"] = str(error)
            results.append(entry)
        report = {
            "mode": "simulation+real_guard", "ran_at": int(time.time()),
            "claimed": len(candidates),
            "already_complete": sum(r["download"] == "already_complete" for r in results),
            "downloaded": sum(r["download"] == "downloaded" for r in results),
            "already_downloaded": sum(r["download"] == "already_downloaded" for r in results),
            "download_failed": sum(r["download"] == "download_failed" for r in results),
            "blocked": sum(r["download"] in ("blocked", "blocked_resolve") for r in results),
            "imported": sum(r["library"] == "imported" for r in results),
            "items": results,
        }
        return report
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scansci-python", type=Path, required=True,
                        help="Path to the scansci suite's venv Python (must be on D:, bundle certifi).")
    parser.add_argument("--reviewed-sources", action="store_true")
    args = parser.parse_args()
    if os.name == "nt" and ROOT.drive.upper() != "D:":
        print("Orchestrator stopped: D_drive_required")
        raise SystemExit(2)
    try:
        report = run(args.scansci_python, args.reviewed_sources)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Problem as error:
        print("Orchestrator stopped: " + str(error))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
