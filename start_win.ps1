# ValueShield — Windows 一键启动脚本
# 用法：右键以 PowerShell 运行，或执行：
#   powershell -ExecutionPolicy Bypass -File start_win.ps1 [-Port 8501]
param(
    [int]$Port = 8501
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir    = Join-Path $ScriptDir ".venv"
$PythonExe  = Join-Path $VenvDir "Scripts\python.exe"

# 检查虚拟环境
if (-not (Test-Path $PythonExe)) {
    Write-Host "❌ 虚拟环境不存在，请先运行：.\setup_win.ps1" -ForegroundColor Red
    exit 1
}

Write-Host "=== ValueShield 启动中 ===" -ForegroundColor Cyan
Write-Host "访问地址：http://localhost:$Port"
Write-Host "按 Ctrl+C 停止服务"
Write-Host ""

Set-Location $ScriptDir

# 使用 python -m streamlit 规避 PATH 未配置 streamlit 命令的问题
& $PythonExe -m streamlit run app.py `
    --server.port $Port `
    --server.headless true `
    --browser.gatherUsageStats false
