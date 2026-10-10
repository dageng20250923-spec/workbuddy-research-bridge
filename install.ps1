# PaperFlow 一键安装脚本（Windows / PowerShell）
#
# 作用：在项目目录下创建一个完全隔离的 Python 虚拟环境，并安装项目所需依赖。
# 特点：
#   - 不修改系统级 Python、不写入系统 PATH、不影响本机其他 Python 环境。
#   - 只安装本项目自己的依赖（pypdf）；scansci-pdf 套件由其自身独立 venv 提供。
#   - 幂等：可重复运行，已存在 .venv 时直接复用并补齐依赖。
#
# 用法（在项目根目录 D:\Dev\wechat-zotero-importer 打开 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File install.ps1
# 或在 PowerShell 中：
#   .\install.ps1

$ErrorActionPreference = "Stop"

# 1. 定位 Python。优先使用可用的 python 命令；允许通过 -PythonExe 覆盖。
param(
    [string]$PythonExe = ""
)

if ($PythonExe -ne "") {
    $py = $PythonExe
} else {
    $py = $null
    foreach ($candidate in @("python", "py -3")) {
        try {
            $v = & $candidate --version 2>$null
            if ($v -match "Python 3") { $py = $candidate; break }
        } catch { }
    }
}

if (-not $py) {
    Write-Host "[错误] 未找到可用的 Python 3。请先安装 Python 3.10+，或使用 -PythonExe 指定路径。" -ForegroundColor Red
    exit 1
}

Write-Host "[1/4] 使用 Python: $py" -ForegroundColor Cyan
& $py --version

# 2. 创建隔离虚拟环境（幂等）
$venvDir = Join-Path $PSScriptRoot ".venv"
if (-not (Test-Path (Join-Path $venvDir "Scripts\python.exe"))) {
    Write-Host "[2/4] 创建隔离虚拟环境 .venv ..." -ForegroundColor Cyan
    & $py -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 创建虚拟环境失败。" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "[2/4] 虚拟环境 .venv 已存在，跳过创建。" -ForegroundColor Cyan
}

$venvPy = Join-Path $venvDir "Scripts\python.exe"

# 3. 安装项目依赖
Write-Host "[3/4] 安装项目依赖 (requirements.txt) ..." -ForegroundColor Cyan
& $venvPy -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] 依赖安装失败。" -ForegroundColor Red; exit 1 }

# 4. 自检：确认 pypdf 可导入
Write-Host "[4/4] 自检依赖 ..." -ForegroundColor Cyan
& $venvPy -c "import pypdf; print('pypdf', pypdf.__version__, 'OK')"
if ($LASTEXITCODE -ne 0) { Write-Host "[错误] pypdf 导入失败。" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "安装完成。" -ForegroundColor Green
Write-Host "项目 Python: $venvPy"
Write-Host "此环境完全隔离，不影响本机其他 Python 环境。"
Write-Host "下一步请阅读 docs/用户手册.md 完成 Zotero/Obsidian/微信配置。"
