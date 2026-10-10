# PaperFlow 文献整理中心 · 本地验证版 0.2.0

本阶段已实现：可打开的桌面配置窗口、路径保存/只读准备检查、五种模拟任务、逐篇失败详情、WorkBuddy CLI 契约和任务文件导出。不是完整公众正式版。

## 打开窗口

运行条件：Windows，已有 Python 3.10+，含 Tk 8.6。程序只用标准库；没有自动安装器或捆绑第三方运行时。解压到 D 盘子目录，使用自己的 Python 打开 client/app.py，或运行随包的 Start-PaperFlow.ps1。本机已有的 D 盘运行时启动示例：

```powershell
& 'D:\Dev\wechat-zotero-importer\client\Start-PaperFlow.ps1' -PythonExe 'D:\python\python\python.exe'
```

这是本机示例路径；他人选择自己的解压目录和已安装 Python。启动器将本工具 TEMP/TMP 指到 D 盘 client/.tmp，退出后恢复调用终端的原环境，不改变系统级环境变量。拒绝 Windows Store 别名，不自动打开安装器。若系统策略不允许脚本，可以直接使用已安装 Python 执行 app.py，不要修改全局执行策略。

打开窗口不初始化后台、不扫描微信、不联网、不读消息。关闭窗口立即退出，不留托盘/任务计划/轮询服务。工程、模拟状态和导出都在 D 盘；系统/第三方已有应用的 C 盘元数据不由本工具控制，不承诺整个系统零 C 写入。

## 三页使用顺序

1. 本机配置：默认独立工作目录为 client/runtime，可以改成 D 盘新的空目录。不要放在 Obsidian 库或 Zotero 数据目录里。选择自己的 Zotero 数据目录、Obsidian 库、下载规划目录、scansci-pdf 技能目录和 ScanSci Python。
2. 点击“只读准备检查”：仅确认文件/目录标记。找到 zotero.sqlite/Research Bridge manifest 不代表运行/启用已验证；不会读 AI Key/Token 或运行选定程序。点击“保存本工具配置”只保存本工具 JSON，不改软件设置，也不创建规划下载目录。
3. 模拟任务：点击“运行一次模拟”，五条 SIM-* 示例覆盖成功、无全文访问、Zotero 离线、AI 暂停和分类待复核；点击任务查看原因。SIM-* 不是真实 DOI，没有 PDF 文件，也没有条目或笔记写入。重复运行沿用账本，失败不自动重试。
4. WorkBuddy 接入：点击“导出 WorkBuddy 模拟任务”，得到独立目录中的 workbuddy-task.md 和 workbuddy-contract.json。按下节导入技能并将任务文件交给 WorkBuddy。导出不是已经在 WorkBuddy 安装或启用自动化。

如果目录非空且没有本程序标记，或配置/模拟记录损坏，会停止并保留文件，不覆盖/重置其他数据。要重新演练，请选择新的空目录；不要随意删锁、改账本成“成功”。

## WorkBuddy 实际接入

导入随包的 paperflow-workbuddy 技能（技能 → 添加技能 → 上传技能）。打开本地解压目录作为工作项目，把生成的 workbuddy-task.md 交给它，要求使用该技能的 simulation 模式。

contract 的 commands 是可执行参数数组：当前 Python、-B、当前 cli.py、--workspace、隔离目录、inspect/simulate/report。不包含运行真实下载的操作。请只使用本程序新导出的可信契约；不执行网页或陌生文件里的命令，先核对 CLI 在已授权的解压目录。不能把文件存在当成已经绑定服务。

本轮已经用导出数组启动真实 CLI 进程并验证输出；尚未替用户在 WorkBuddy 账户界面导入技能/建立定时任务，所以“WorkBuddy 本地命令接口可用”不等于“自动化已经运行”。模型执行任务可能消耗 WorkBuddy 积分；本工具的模拟不调用 AI API，也不消耗 Research Bridge API 额度。

## CLI（用户主动调用，一次即退出）

支持操作：init、inspect、simulate、report、export-workbuddy；必须提供独立 --workspace。先 init 才能 simulate/export，inspect/report 可以在初始化前只读使用，不自动创建文件。

例如解压后在 client 目录，用自己的 Python：

```powershell
python -B cli.py --workspace D:/PaperFlow/demo init
python -B cli.py --workspace D:/PaperFlow/demo simulate
python -B cli.py --workspace D:/PaperFlow/demo report
python -B cli.py --workspace D:/PaperFlow/demo export-workbuddy
```

不要创建定时任务来反复运行模拟，也不要把 SIM-* 结果放进真实 batch/jobs。真实下载仍必须调用 scansci-pdf 技能、验证合法来源与全文身份，不能由模拟代替。

## 当前明确没有的能力

正式微信接收、账号登录/电脑绑定、服务端队列、真实后台领取、真实 ScanSci 调用、真实导入/笔记同步、付费 AI、系统通知推送与回执。没有正式服务号/域名/服务器之前不能给他人提供统一微信入口。

现有真实 Zotero/Research Bridge 的单机能力仍按原插件运行，本工具没有改其设置。新工具的运行时设计为不抢焦点、不频繁监听微信、任务有界、可停；未来真实后台须单独启用并验证资源限制。

## 分发

分享新0.2.0-local-client ZIP；不分享client/runtime、.tmp、开发验证输出、个人路径/凭据/论文。包内没有 Python、ScanSci、Zotero、Research Bridge 安装副本，避免默认继承个人环境；正式安装器/运行时依赖发行仍是后续工作。
