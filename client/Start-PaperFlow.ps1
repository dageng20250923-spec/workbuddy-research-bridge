param([string]$PythonExe = '', [string]$Workspace = '')
$ErrorActionPreference = 'Stop'
$clientFolder = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not $clientFolder.StartsWith('D:\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Please extract PaperFlow into a D drive subfolder.'
}
$taskTemp = Join-Path $clientFolder '.tmp'
New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
if ((Get-Item -LiteralPath $taskTemp).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw 'Temporary folder must not be a link or junction.'
}
if (-not $PythonExe) {
    $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($pythonCommand) { $PythonExe = $pythonCommand.Source }
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw 'Python 3.10+ with Tk is required. Pass -PythonExe with your installed Python path. No software will be installed automatically.'
}
if ($PythonExe -match '\\WindowsApps\\') {
    throw 'The Windows Store alias is not a configured Python runtime. Pass -PythonExe explicitly; no installer will be opened.'
}
if (-not $Workspace) { $Workspace = Join-Path $clientFolder 'runtime' }
$entryScript = Join-Path $clientFolder 'app.py'
$taskEnvBefore = @{ TEMP=$env:TEMP; TMP=$env:TMP; PYTHONDONTWRITEBYTECODE=$env:PYTHONDONTWRITEBYTECODE; PYTHONIOENCODING=$env:PYTHONIOENCODING }
try {
    $env:TEMP=$taskTemp
    $env:TMP=$taskTemp
    $env:PYTHONDONTWRITEBYTECODE='1'
    $env:PYTHONIOENCODING='utf-8'
    & $PythonExe -B -c 'import sys, tkinter; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ with Tk is required; no installation will be performed.' }
    # Argument array: never evaluate a user-provided path as shell text.
    & $PythonExe -B $entryScript --workspace $Workspace
    if ($LASTEXITCODE -ne 0) { throw 'PaperFlow did not start. No background service was enabled.' }
} finally {
    foreach ($taskEnvKey in $taskEnvBefore.Keys) {
        [System.Environment]::SetEnvironmentVariable($taskEnvKey,$taskEnvBefore[$taskEnvKey],'Process')
    }
}
