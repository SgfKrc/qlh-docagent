# Patchouli 一键安装（npm 式）：editable 安装 + 全局命令注册 + 默认根配置 + 自检
# 用法（任意目录）：
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -RepoRoot G:\C\PYT\qlh -Python <python.exe>
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
if (-not $Python) {
    $venvPy = Join-Path $RepoRoot ".venv-test\Scripts\python.exe"
    if (Test-Path $venvPy) { $Python = $venvPy } else { $Python = (Get-Command python).Source }
}
Write-Host "== Patchouli 安装 ==" -ForegroundColor Magenta
Write-Host "RepoRoot : $RepoRoot"
Write-Host "Python   : $Python"

# 1) editable 安装（提供 patchouli / patchouli-shelf console scripts）
& $Python -m pip install -e (Join-Path $RepoRoot "tools\docagent") --quiet
Write-Host "[1/4] editable 安装完成（tools/docagent）"

# 2) 写用户默认根配置（~/.patchouli/config.json）——任意目录调用不再失焦
& $Python -c "import sys; sys.path.insert(0, r'$RepoRoot\tools\docagent'); from patchouli.roots import write_config; print('[2/4] 默认根配置:', write_config(r'$RepoRoot'))"
& $Python -c "import sys; sys.path.insert(0, r'$RepoRoot\tools\docagent'); from patchouli.roots import add_library; print('[2.5/4] 注册库:', add_library(r'$RepoRoot')['name'])"

# 3) 全局命令 shim + PATH 注册（幂等）
$binDir = Join-Path $env:USERPROFILE "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$shim = Join-Path $binDir "patchouli.cmd"
$content = "@echo off`r`n`"$Python`" -m patchouli %*`r`n"
Set-Content -Path $shim -Value $content -Encoding ASCII
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$binDir", 'User')
    Write-Host "[3/4] PATH 已注册: $binDir（新终端生效）"
} else {
    Write-Host "[3/4] PATH 已包含: $binDir"
}

# 4) 自检（从当前目录调用，验证 root 解析）
Write-Host "[4/4] 自检:"
& $shim summary
Write-Host ""
# 4.5) 安装自带 skill（含本工具体检流程）到项目 skills 目录
$skillSrc = Join-Path $RepoRoot "tools\docagent\skills\docagent\SKILL.md"
if (Test-Path $skillSrc) {
    $skillDst = Join-Path $RepoRoot ".reasonix\skills\docagent"
    New-Item -ItemType Directory -Force -Path $skillDst | Out-Null
    Copy-Item $skillSrc (Join-Path $skillDst "SKILL.md") -Force
    Write-Host "[4.5/4] skill 已同步: $skillDst"
}
Write-Host "完成。命令: patchouli〔TUI〕 / patchouli summary / patchouli json / patchouli setup / patchouli lib" -ForegroundColor Magenta
Write-Host "MCP（可选）: reasonix mcp add patchouli -- `"$Python`" -m patchouli.mcp_server" -ForegroundColor Magenta
