# ValueShield — Windows 一键环境初始化脚本
# 用法：右键以 PowerShell 运行，或执行：
#   powershell -ExecutionPolicy Bypass -File setup_win.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir   = Join-Path $ScriptDir ".venv"

Write-Host "=== ValueShield 环境初始化 ===" -ForegroundColor Cyan
Write-Host "项目目录：$ScriptDir"
Write-Host "虚拟环境：$VenvDir"
Write-Host ""

# 检查 Python
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(1[0-9]|[2-9]\d)") {
            $python = $cmd
            Write-Host "✅ 检测到 $ver" -ForegroundColor Green
            break
        }
    } catch { }
}

if (-not $python) {
    Write-Host "❌ 未找到 Python 3.10+，请先安装：https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

# 创建虚拟环境
if (-not (Test-Path $VenvDir)) {
    Write-Host "📦 创建虚拟环境 .venv ..."
    & $python -m venv $VenvDir
} else {
    Write-Host "♻️  虚拟环境已存在，跳过创建。"
}

$pip = Join-Path $VenvDir "Scripts\pip.exe"
$pythonExe = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "⬆️  升级 pip ..."
& $pythonExe -m pip install --upgrade pip --quiet

Write-Host "📚 安装依赖（requirements.txt）..."
& $pip install -r (Join-Path $ScriptDir "requirements.txt") --quiet

Write-Host ""
Write-Host "🎉 初始化完成！" -ForegroundColor Green
Write-Host ""
Write-Host "下一步：运行  .\start_win.ps1  启动服务"
