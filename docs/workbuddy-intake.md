# WorkBuddy 微信助理收件入口

更新日期：2026-10-12。配套脚本更新，Zotero 插件仍使用 0.7.4。

## 功能与边界

先由用户自行配置 WorkBuddy 微信助理，使消息/文件能够到达自己的助手会话；然后由助手在本机调用以下脚本。PaperFlow 不替用户绑定微信、不读聊天数据库、不操作微信窗口，也不建立常驻公网服务。

| 收件类型 | 入口 | 写入位置与后续行为 |
|---|---|---|
| DOI / 简短公众号链接 | `batch/enqueue.py` | 仅追加本地队列的 pending 候选；重复候选忽略，不下载、不改已有状态 |
| 已由 WorkBuddy 提供本地路径的 PDF | `batch/intake_file.py` | 复制进 `batch/inbox/`，按 SHA256 去重，然后执行现有论文/个人资料分流；可能立即调用 Zotero 导入接口 |

没有收件参数直接运行 `intake_file.py` 会处理已有 inbox，并非只读。`--list` 才是只读查看。PDF 大小上限 60 MB，文件类型入口按扩展名筛选，不等于已完成论文身份或恶意文件检查。个人资料判断沿用文件名关键词规则，可能误判。重复文件跳过不会自动恢复用户后来从 Zotero 删除的条目。

入口识别 DOI 的范围比发布版下载支持范围宽；不支持的 DOI 会在后续阶段失败/等待复核。公众号入口目前匹配 `https://mp.weixin.qq.com/s/<短链接>`，不保证其他 URL 形式可入队或正文一定可解析。本文不保证所有 DOI 都能下载。

## 1. 本机前置配置

按根目录[用户手册](../用户手册.md)安装 Python 环境、Zotero 0.7.4 插件和 Obsidian Research Bridge。Zotero 插件需启用，导入接口可用；AI 阅读需用户自己的提供商和凭据，可能计费。

以下命令假定 PowerShell 当前目录是项目根目录，例如 `D:\PaperFlow`。代码放在发布仓库根目录运行，而不是工程中更上层的目录。

首次使用 DOI 队列，若没有现成队列，先建立空数据库：

```powershell
.\.venv\Scripts\python.exe -B -c "from pathlib import Path; from receiver.receiver import Store; Store(Path('receiver/runtime/queue.sqlite3'))"
```

这是创建本地队列，不会启动回调服务器。首次入队要用 `--account-key` 指定自己的非敏感标签；不要填写真实微信号。之后若队列只有一个账号键，脚本可沿用；多账号必须显式指定。

## 2. DOI / 公众号链接入队

```powershell
# 示例占位 DOI：替换为自己的目标论文
.\.venv\Scripts\python.exe -B batch\enqueue.py --doi "10.1038/s41566-026-02019-6" --account-key "local-research"
.\.venv\Scripts\python.exe -B batch\enqueue.py --url "https://mp.weixin.qq.com/s/REPLACE_WITH_ARTICLE" --account-key "local-research"
.\.venv\Scripts\python.exe -B batch\enqueue.py --list
```

助手也可用 `--text` 提取整段消息里的候选。调用工具时使用独立参数传递消息，不把未经处理的消息拼接成可执行命令。队列输出含论文链接，不应当公开；真实队列数据库不随仓库分发。

入队后仍需要单独执行/配置自动化运行 `orchestrate.py`：

```powershell
.\.venv\Scripts\python.exe -B batch\orchestrate.py --scansci-python "D:\path\to\scansci-venv\Scripts\python.exe"
```

失败时向用户逐篇汇报 DOI/来源、错误原因和需要的操作，不得把下载失败一律说成 scansci-pdf 没安装，也不得只列成功项。下载接入仍调用 scansci-pdf 适配链；没有已验证的全文就不当作下载成功。

## 3. PDF 文件收件

```powershell
.\.venv\Scripts\python.exe -B batch\intake_file.py --file "D:\path\to\paper.pdf" --state-path "D:\path\to\wechat-zotero-importer\standalone-state.json" --archive "D:\PaperFlow\个人资料"
.\.venv\Scripts\python.exe -B batch\intake_file.py --list
```

`--file` / `--dir` 接收的是 WorkBuddy 已取得的本地文件路径，不是微信界面里附件的显示名。此脚本不会替用户下载尚未落盘的附件。请将 `--state-path` 指向自己插件真实状态文件；发布版默认 `D:\PaperFlow\standalone-state.json` 是示例，不是作者的数据目录。路径设置错误会影响与插件已完成记录的去重联动。

来源文件保持原位。收件账本 `batch/inbox/.intake-ledger.json` 会记录本地来源路径，应留在本机，不上传。

## 4. WorkBuddy 自动化建议

在自己的 WorkBuddy 中配置入口调用和执行时间：消息到达后可仅入队，统一在用户选定时间运行整理。PDF 收件脚本本身会继续执行分流/导入，应在用户授权的时机调用。

可给助手以下工作说明，并填好自己机器的路径：

> 收到 DOI / 支持的公众号链接后调用 enqueue.py 入队；收到有本地路径的 PDF 后，在允许整理的时机调用 intake_file.py。只处理本用户提供的文件。不操作微信窗口，不读取聊天数据库，不改动来源文件。下载仍使用 scansci-pdf 适配链，失败逐篇明确告知。之后复用 Zotero 导入、待识别分类和 Research Bridge 同步。智能分类只对授权的待识别条目执行；AI 配置与费用由用户决定。

该段是配置说明，不会自动在 WorkBuddy 创建任务。作者已在自己的 WorkBuddy 环境测试；发布前另做离线检查，不代表其他账号/电脑无须配置或已完成全平台测试。

## 5. Nature 授权跳转与旧隧道

发布目录中的 Nature 适配改用保持 cookie 的 Session 处理授权中转，仍保留 PDF/hash/论文身份复核。下载失败可能来自网络、访问权限、源站变更或套件依赖，需要以实际错误为准。不要仅凭失败回复就认定未安装。

如果选择本文的 WorkBuddy 微信助理入口，不依赖原测试公众号的临时 HTTPS 隧道。只有仍用旧回调接收方案的用户才需要排查隧道和服务是否在线；`receiver/` 保留旧方案源码，不会自动启动或切换入口。

本次未同步主工程另有的灰色下载来源扩展，未改 Zotero 插件版本，也未修改本机 WorkBuddy 自动化与 AI 设置。
