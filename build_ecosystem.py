"""Package the full PaperFlow ecosystem into a single distributable ZIP.

Unlike build_local.py (client-only) and build_preparation.py (workbuddy-only),
this script packages the ENTIRE project — source code, install script,
requirements, docs, tests, workbuddy skill, and the 0.7.0 plugin backup —
while strictly excluding anything that must never ship:

  - the isolated .venv / any third-party runtime
  - downloads / jobs / reports / runtime state (personal data)
  - bridge-token and other credentials
  - personal-document archives (个人资料)
  - reviewed-sources.json (contains personal WeChat URLs / DOIs)

Every included path is resolved and checked to be a regular file inside the
project root; the archive carries a MANIFEST.json with per-file SHA256 and an
explicit contains_credentials:false assertion, then passes an integrity test.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parent

# Files explicitly included, relative to ROOT. Everything else is excluded.
# This is an ALLOWLIST — safer than a deny-list for personal-data safety.
ALLOWLIST = (
    # top-level
    "install.ps1",
    "requirements.txt",
    "HANDOFF.md",
    # docs
    "docs/2026-10-09-end-to-end-automation.md",
    "docs/2026-10-09-wechat-official-account-guide.md",
    "docs/2026-10-10-pdf-dir-automation.md",
    "docs/2026-10-10-wechat-entry-personal.md",
    "docs/用户手册.md",
    # batch (source only, no downloads/jobs/reports)
    "batch/scan_pdf_dir.py",
    "batch/orchestrate.py",
    "batch/library_handoff.py",
    "batch/observe_handoff.py",
    "batch/scansci_adapter.py",
    "batch/scansci_nature_source.py",
    "batch/paper_batch.py",
    "batch/README.md",
    "batch/tests/test_library_handoff.py",
    "batch/tests/test_paper_batch.py",
    "batch/tests/test_scansci_adapter.py",
    # receiver (source + tests, no runtime/queue)
    "receiver/receiver.py",
    "receiver/README.md",
    "receiver/tests/test_pairing.py",
    "receiver/tests/test_receiver.py",
    # client (source + launcher + docs, no dist/runtime/.tmp)
    "client/app.py",
    "client/core.py",
    "client/cli.py",
    "client/build_local.py",
    "client/Start-PaperFlow.ps1",
    "client/README.md",
    # workbuddy (skill + build tooling + docs, no .tmp/dist)
    "workbuddy/build_preparation.py",
    "workbuddy/使用手册.md",
    "workbuddy/验证记录.md",
    "workbuddy/paperflow-workbuddy/SKILL.md",
    "workbuddy/paperflow-workbuddy/references/workflow.md",
    "workbuddy/paperflow-workbuddy/references/local-client.md",
    "workbuddy/paperflow-workbuddy/scripts/report_jobs.py",
    # plugin 0.7.0 backup (source + XPI, no state/ledgers)
    "backup/wechat-zotero-importer-0.7.0/extracted/bootstrap.js",
    "backup/wechat-zotero-importer-0.7.0/extracted/classifier.js",
    "backup/wechat-zotero-importer-0.7.0/extracted/standalone-watcher.js",
    "backup/wechat-zotero-importer-0.7.0/extracted/migration.js",
    "backup/wechat-zotero-importer-0.7.0/extracted/preferences.js",
    "backup/wechat-zotero-importer-0.7.0/extracted/preferences.xhtml",
    "backup/wechat-zotero-importer-0.7.0/extracted/prefs.js",
    "backup/wechat-zotero-importer-0.7.0/extracted/manifest.json",
    "backup/wechat-zotero-importer-0.7.0/extracted/classification-profiles.json",
    "backup/wechat-zotero-importer-0.7.0/extracted/taxonomy.json",
    "backup/wechat-zotero-importer-0.7.0/wechat-zotero-importer@example.com.xpi",
)

# Files that must NEVER be present in any allowlisted path (defense in depth).
FORBIDDEN_FRAGMENTS = (
    "bridge-token", "reviewed-sources.json", ".sqlite3", ".archive-ledger.json",
)


def collect() -> dict[str, Path]:
    members: dict[str, Path] = {}
    for name in ALLOWLIST:
        path = ROOT / name
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_relative_to(ROOT):
            raise ValueError(f"unsafe_input: {name}")
        if not path.is_file():
            raise ValueError(f"missing_input: {name}")
        if any(frag in name for frag in FORBIDDEN_FRAGMENTS):
            raise ValueError(f"forbidden_input: {name}")
        members[name] = path
    return members


def write_archive(path: Path, members: dict[str, Path]):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in members.items():
            archive.write(source, name)
        manifest = {
            "package": "PaperFlow ecosystem (local edition)",
            "contains_credentials": False,
            "third_party_runtime_bundled": False,
            "personal_data_bundled": False,
            "files": {name: hashlib.sha256(source.read_bytes()).hexdigest()
                      for name, source in members.items()},
        }
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("archive_integrity_failed")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "member_count": len(members) + 1,
    }


def main():
    if os.name == "nt" and ROOT.drive.upper() != "D:":
        print("Packaging stopped: D_drive_required")
        raise SystemExit(2)

    members = collect()
    dist = ROOT / "client" / "dist"
    if dist.is_symlink() or not dist.resolve().is_relative_to(ROOT):
        print("Packaging stopped: unsafe_output_directory")
        raise SystemExit(2)
    dist.mkdir(exist_ok=True)

    out = dist / "PaperFlow-0.2.0-ecosystem.zip"
    if out.exists():
        print("Packaging stopped: artifact_exists_no_overwrite (" + out.name + ")")
        raise SystemExit(2)

    result = write_archive(out, members)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
