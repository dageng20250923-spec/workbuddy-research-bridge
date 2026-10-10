# PaperFlow

PaperFlow is a personal literature-management workflow that turns PDFs received over WeChat into a cleanly classified Zotero library and Obsidian reading notes — automatically.

> **Status:** Local single-machine tool (v0.2.0). This is **not** a turn-key public SaaS. It is designed to run on the author's own Windows machine; other users can adapt it by providing their own local paths and accounts. See [用户手册.md](./用户手册.md) for the full install/usage guide (Chinese).

## What it does

The pipeline has four stages, each owned by a small, single-purpose module:

| Stage | Module | Automatic |
|---|---|---|
| 1. Scan a WeChat file folder; split academic papers from personal documents | `batch/scan_pdf_dir.py` | yes |
| 2. Download papers (by DOI/link, via the scansci-pdf suite) | `batch/orchestrate.py` + `batch/scansci_adapter.py` | yes |
| 3. Import into Zotero and classify by imaging method | Zotero plugin `/wechat-zotero/import` + classifier | yes |
| 4. Sync to Obsidian + AI reading | Obsidian "Research Bridge" plugin | semi (see below) |

**About "automatic AI literature reading":** stage 4 relies on the third-party [Research Bridge](https://obsidian.md) Obsidian plugin. PaperFlow only delivers items into Zotero and *read-only observes* whether Research Bridge has produced a note / AI task. The AI reading itself is triggered by Research Bridge inside Obsidian and requires the user to configure an AI provider (API key) there. Without that, you get import + literature cards, but no AI reading.

## Repository layout

```
├── batch/                  core scripts (scan, orchestrate, import, observe)
│   └── tests/              unit tests
├── receiver/               WeChat callback receiver (service skeleton)
├── client/                 local Tk desktop client (config window + simulation)
├── workbuddy/              WorkBuddy skill package (paperflow-workbuddy)
├── backup/                 Zotero plugin 0.7.0 source + XPI (for reference)
├── docs/                   guides & the user manual
├── install.ps1             one-click isolated-env installer (Windows)
├── requirements.txt        project dependencies (pypdf only)
├── build_ecosystem.py      packages the whole ecosystem into a ZIP (allowlist)
└── PaperFlow-0.2.0-ecosystem.zip   prebuilt distributable archive
```

## Requirements

- Windows, Python 3.10+ (3.11 recommended)
- Zotero 7.x / 9.x + the "微信 PDF 自动整理" plugin (0.7.0, XPI in `backup/`)
- Obsidian + "Research Bridge" plugin (for stage 4)
- scansci-pdf suite with its own venv (for downloads)
- WeChat (PC) as the PDF source

## Quick start

```powershell
# 1. create an isolated venv + install deps (does NOT touch system Python)
powershell -ExecutionPolicy Bypass -File install.ps1

# 2. scan & route PDFs (papers -> Zotero, personal docs -> local archive)
.\.venv\Scripts\python.exe batch\scan_pdf_dir.py --source "D:\path\to\wechat\files" --archive "D:\PaperFlow\个人资料"

# 3. end-to-end download -> import (points at the scansci suite's own venv)
.\.venv\Scripts\python.exe batch\orchestrate.py --scansci-python "D:\path\to\scansci-venv\Scripts\python.exe"
```

Full configuration steps (Zotero plugin, WeChat folder, Obsidian/Research Bridge) are in [用户手册.md](./用户手册.md).

## Design principles

- **Isolated runtime** — the installer builds a project-local `.venv`; nothing is written to system Python or PATH.
- **Allowlist packaging** — `build_ecosystem.py` packages only explicitly listed source files. No credentials, personal data, downloads, or third-party runtimes are ever bundled (asserted in `MANIFEST.json`).
- **Read-only observation** — the workflow never overwrites personal notes or existing classifications.
- **Fail visible** — download/import failures stay in the ledger and are reported per-article; nothing is silently marked successful.
- **No WeChat UI automation** — the tool never opens, searches, or controls the WeChat window.

## Not included (by design)

Personal WeChat account bindings, WeChat file paths, Zotero/Obsidian data directories, bridge tokens, the scansci-pdf suite, and any paper PDFs or personal documents. These are user-specific and must be provided by each user.

## License

MIT — see [LICENSE](./LICENSE).
