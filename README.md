# PaperFlow

## 中文简介

### 2026-10-12：WorkBuddy 收件入口更新

新增 [DOI / 公众号链接入队入口](./batch/enqueue.py) 与 [PDF 收件入口](./batch/intake_file.py)，将用户自己配置的 WorkBuddy 微信助理收到的消息衔接到本地处理流程；不打开、检索或操控微信窗口。PDF 只复制、按 SHA256 去重，再复用现有目录分流与 Zotero 导入接口。

同时同步发布目录中的 Nature 授权跳转适配及排查说明。此次是配套脚本与文档更新，Zotero XPI 仍为 **0.7.4**。主工程另有下载来源扩展，此次未纳入；本发布版下载仍限既有受支持来源，不代表任意 DOI 都可下载。入口能识别 DOI 不等于该 DOI 已有下载适配。

作者已在自己的 WorkBuddy 环境测试运行；其他用户仍须配置接收会话、队列、来源目录、插件状态路径和 Research Bridge。详细步骤见 [WorkBuddy 收件指南](./docs/workbuddy-intake.md)。本仓库不会替用户绑定微信、创建自动化任务或提供常驻公网接收服务。

**让 Zotero 管论文，让 Obsidian 承接阅读与思考。**

PaperFlow 是一个开源的本地科研文献整理工作流，将微信来源文件分流、文献下载、Zotero 分类与 Obsidian 笔记衔接起来，减少反复下载、命名和归档的机械操作。适合已经使用 Zotero / Obsidian、愿意配置本地环境的研究者。

完成所需配置并主动运行相应模块后，可处理已落盘的 PDF，或解析接收队列中的受支持链接 / DOI；通过 scansci-pdf 适配层尝试下载有合法访问权限的全文，再交给 Zotero 插件导入与整理。下载失败、导入待处理等情况会保留在任务记录中，便于逐篇检查，而不是只显示成功项。

**PDF 分流：**扫描用户配置目录中已经落盘的文件，用文件名关键词将疑似个人资料与文献分开处理；个人资料复制归档，文献交给 Zotero。该规则可能误判，不等于能可靠识别所有文件，也不会主动从微信聊天中下载尚未落盘的附件。

**关键词分类：**当前默认工作流重点是光学成像方法，依据题名、摘要及可提取正文中的关键词，辅助区分 OCT、光纤成像、计算成像等方法分支。不将疾病或器官名称作为默认分类主线；证据不足的结果保留待复核，不承诺分类零误差。源 PDF 保留不动，附件命名由 Zotero 插件在其管理的副本上处理。

Obsidian 文献卡片及 AI 阅读依赖另行安装、配置的 **Research Bridge**。PaperFlow 不自带 AI 模型或 API 密钥，也不保证所有公众号链接都能解析、所有 DOI 都能获得全文；付费模型的费用由对应服务提供方收取。

**版本边界：**目前是 Windows 本地工具与组件集合，不是安装后即可使用的公共在线服务。`client/` 桌面窗口仍是配置与模拟验证版，不会直接运行真实下载或导入；WorkBuddy 技能需要用户导入和配置，不能把任务导出视为自动化已经启用。微信链接接收入口需要单独接入，本工具不会读取任意个人聊天，也不自动打开、搜索或操作微信窗口。

### 两级分类与每日整理

仓库现提供 [新版用户手册](./用户手册.md)、[智能分类脚本](./batch/classify_smart.py) 和 [0.7.4 插件安装包](./backup/wechat-zotero-importer-0.7.0/wechat-zotero-importer@example.com-0.7.4.xpi)。请先配置自己的目录、账号与“待识别”集合编号，再使用以下扩展流程。

- **两级分类：**导入时先用关键词规则兜底；对“待识别”文献，再由 WorkBuddy 中的智能体阅读题名和摘要、提出维度与类别，经新版插件的 `/wechat-zotero/classify` 接口回写到 `微信文献 → 维度 → 类别`。扩展维度可涵盖成像方法以外的学科；证据不足时保留待识别，不强行归类。脚本本身不包含分类模型，也不会自行完成推理。
- **每日整理时间：**0.7.4 插件设置页可保存 `HH:MM` 时间；需要另行建立 WorkBuddy“时间同步”和“文献整理”两个自动化任务才能联动。按手册方案，次日 00:05 同步，再按新时间整理；不是保存即生效，也不会因安装插件而自动建立调度。
- **兼容声明：**0.7.4 安装包的 manifest 将上限声明为 Zotero `10.0.*`；这是版本范围声明，不代表本仓库已经完成 Zotero 10 的运行兼容性验证。

智能分类脚本是连接智能体与插件的接口工具，不是独立的 AI 分类器；发布版不包含作者的集合编号，请按手册 §5.4 将 `PENDING_COLLECTION` 占位值替换为自己库中的编号。建议先用一篇文献验证，再启用自己配置的自动化。每日整理与时间同步任务也需要用户另行创建；仓库不会替用户创建或启用任务。

开始前请阅读 [用户手册](./用户手册.md) 与 [桌面客户端说明](./client/README.md)。源码采用 MIT 许可证；第三方软件、模型服务与论文全文的许可独立适用。欢迎通过 Issues 反馈问题或通过 Pull Request 参与完善。

## English overview

PaperFlow is an open-source local literature-management workflow connecting PDF processing and supported DOI/link downloads with Zotero organization and Obsidian reading notes. Individual processing stages require local configuration and an explicit invocation or a separately configured automation.

> **Status:** Local single-machine tool (v0.2.0). This is **not** a turn-key public SaaS. It is designed to run on the author's own Windows machine; other users can adapt it by providing their own local paths and accounts. See [用户手册.md](./用户手册.md) for the full install/usage guide (Chinese).

## What it does

The pipeline has five stages, each owned by a small, single-purpose module:

| Stage | Module | Automatic |
|---|---|---|
| 1. Scan a WeChat file folder; split academic papers from personal documents | `batch/scan_pdf_dir.py` | yes |
| 2. Download papers (by DOI/link, via the scansci-pdf suite) | `batch/orchestrate.py` + `batch/scansci_adapter.py` | yes |
| 3. Import into Zotero and classify by imaging method | Zotero plugin `/wechat-zotero/import` + classifier | yes |
| 4. Classify pending items into agent-selected dimensions/categories | `batch/classify_smart.py` + plugin 0.7.4 | requires an external agent and local configuration |
| 5. Sync to Obsidian + AI reading | Obsidian "Research Bridge" plugin | semi (see below) |

Here, "automatic" describes processing within an invoked stage, not an out-of-the-box background service. The desktop client is a configuration/simulation tool; it does not execute the live pipeline. Link reception and WorkBuddy task activation must be configured separately. Download support is limited to supported sources with lawful access, and keyword-based classification requires review when evidence is insufficient.

The updated manual, companion classification script and plugin 0.7.4 are included. Set the script's pending-collection key to your own library's key before using it. Classification requires an external agent, and schedule changes require separately configured WorkBuddy automations; neither runs just by installing the desktop client.

**About "automatic AI literature reading":** stage 5 relies on the third-party [Research Bridge](https://obsidian.md) Obsidian plugin. PaperFlow only delivers items into Zotero and *read-only observes* whether Research Bridge has produced a note / AI task. The AI reading itself is triggered by Research Bridge inside Obsidian and requires the user to configure an AI provider (API key) there. Without that, you get import + literature cards, but no AI reading.

## Repository layout

```
├── batch/                  core scripts (scan, orchestrate, import, observe)
│   ├── enqueue.py          WorkBuddy DOI/article-link intake (queue only)
│   ├── intake_file.py      WorkBuddy PDF inbox + existing routing
│   └── tests/              unit tests
├── receiver/               WeChat callback receiver (service skeleton)
├── client/                 local Tk desktop client (config window + simulation)
├── workbuddy/              WorkBuddy skill package (paperflow-workbuddy)
├── backup/                 legacy 0.7.0 source/XPI + current 0.7.4 XPI
├── docs/                   guides & the user manual
├── install.ps1             one-click isolated-env installer (Windows)
├── requirements.txt        project dependencies (pypdf only)
└── build_ecosystem.py      packaging script; review generated archives before sharing
```

## Requirements

- Windows, Python 3.10+ (3.11 recommended)
- Zotero + the "微信 PDF 自动整理" plugin (use 0.7.4 XPI in `backup/`; manifest range `6.999`–`10.0.*`, not a runtime compatibility guarantee)
- An external agent such as WorkBuddy and your own pending-collection key (for stage 4)
- Obsidian + "Research Bridge" plugin (for stage 5)
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
- **Explicit write boundaries** — note observation is read-only. Smart classification `apply` explicitly changes Zotero collections/tags and can replace memberships inside the plugin's collection tree; use it only on reviewed, authorized pending items. It preserves collections outside the plugin tree.
- **Fail visible** — download/import failures stay in the ledger and are reported per-article; nothing is silently marked successful.
- **No WeChat UI automation** — the tool never opens, searches, or controls the WeChat window.

## Not included (by design)

Personal WeChat account bindings, WeChat file paths, Zotero/Obsidian data directories, bridge tokens, the scansci-pdf suite, and any paper PDFs or personal documents. These are user-specific and must be provided by each user.

## License

MIT — see [LICENSE](./LICENSE).
