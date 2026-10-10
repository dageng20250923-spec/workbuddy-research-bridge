# PaperFlow

## 中文简介

**让 Zotero 管论文，让 Obsidian 承接阅读与思考。**

PaperFlow 是一个开源的本地科研文献整理工作流，将 PDF 处理、文献下载、Zotero 分类与 Obsidian 笔记衔接起来，减少反复下载、命名和归档的机械操作。适合已经使用 Zotero / Obsidian、愿意配置本地环境的研究者。

完成所需配置并主动运行相应模块后，可处理已落盘的 PDF，或解析接收队列中的受支持链接 / DOI；通过 scansci-pdf 适配层尝试下载有合法访问权限的全文，再交给 Zotero 插件导入与整理。下载失败、导入待处理等情况会保留在任务记录中，便于逐篇检查，而不是只显示成功项。

当前整理重点是**光学成像方法**：依据题名、摘要及可提取正文中的关键词，辅助区分 OCT、光纤成像、计算成像等方法分支。不将疾病或器官名称作为默认分类主线；证据不足的结果需要复核，不承诺分类零误差。源 PDF 保留不动，附件命名由 Zotero 插件在其管理的副本上处理。

Obsidian 文献卡片及 AI 阅读依赖另行安装、配置的 **Research Bridge**。PaperFlow 不自带 AI 模型或 API 密钥，也不保证所有公众号链接都能解析、所有 DOI 都能获得全文；付费模型的费用由对应服务提供方收取。

**版本边界：**目前是 Windows 本地工具与组件集合，不是安装后即可使用的公共在线服务。`client/` 桌面窗口仍是配置与模拟验证版，不会直接运行真实下载或导入；WorkBuddy 技能需要用户导入和配置，不能把任务导出视为自动化已经启用。微信链接接收入口需要单独接入，本工具不会读取任意个人聊天，也不自动打开、搜索或操作微信窗口。

开始前请阅读 [用户手册](./用户手册.md) 与 [桌面客户端说明](./client/README.md)。源码采用 MIT 许可证；第三方软件、模型服务与论文全文的许可独立适用。欢迎通过 Issues 反馈问题或通过 Pull Request 参与完善。

## English overview

PaperFlow is an open-source local literature-management workflow connecting PDF processing and supported DOI/link downloads with Zotero organization and Obsidian reading notes. Individual processing stages require local configuration and an explicit invocation or a separately configured automation.

> **Status:** Local single-machine tool (v0.2.0). This is **not** a turn-key public SaaS. It is designed to run on the author's own Windows machine; other users can adapt it by providing their own local paths and accounts. See [用户手册.md](./用户手册.md) for the full install/usage guide (Chinese).

## What it does

The pipeline has four stages, each owned by a small, single-purpose module:

| Stage | Module | Automatic |
|---|---|---|
| 1. Scan a WeChat file folder; split academic papers from personal documents | `batch/scan_pdf_dir.py` | yes |
| 2. Download papers (by DOI/link, via the scansci-pdf suite) | `batch/orchestrate.py` + `batch/scansci_adapter.py` | yes |
| 3. Import into Zotero and classify by imaging method | Zotero plugin `/wechat-zotero/import` + classifier | yes |
| 4. Sync to Obsidian + AI reading | Obsidian "Research Bridge" plugin | semi (see below) |

Here, "automatic" describes processing within an invoked stage, not an out-of-the-box background service. The desktop client is a configuration/simulation tool; it does not execute the live pipeline. Link reception and WorkBuddy task activation must be configured separately. Download support is limited to supported sources with lawful access, and keyword-based classification requires review when evidence is insufficient.

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
└── build_ecosystem.py      packaging script; review generated archives before sharing
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
- **Review before distribution** — packaging uses an explicit file list, but generated archives must still be inspected for credentials, personal paths and private data before sharing. No prebuilt ecosystem ZIP is included in this repository.
- **Read-only observation** — the workflow never overwrites personal notes or existing classifications.
- **Fail visible** — download/import failures stay in the ledger and are reported per-article; nothing is silently marked successful.
- **No WeChat UI automation** — the tool never opens, searches, or controls the WeChat window.

## Not included (by design)

Personal WeChat account bindings, WeChat file paths, Zotero/Obsidian data directories, bridge tokens, the scansci-pdf suite, and any paper PDFs or personal documents. These are user-specific and must be provided by each user.

## License

MIT — see [LICENSE](./LICENSE).
