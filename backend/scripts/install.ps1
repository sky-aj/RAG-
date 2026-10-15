# 步骤 1-2：创建虚拟环境并安装依赖
$ErrorActionPreference = "Stop"
# 让中文输出正常显示
chcp 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$backend = Split-Path -Parent $PSScriptRoot
Set-Location $backend

if (-not (Test-Path ".venv")) {
    Write-Host "[1/5] 创建虚拟环境 .venv" -ForegroundColor Cyan
    python -m venv .venv
}

Write-Host "[2/5] 升级 pip" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m pip install --upgrade pip

Write-Host "[3/5] 安装依赖（首次较慢）" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已生成 .env，请填写 LLM_API_KEY 与所需备用模型密钥" -ForegroundColor Yellow
}

Write-Host "[4/5] 生成 DOCX / PDF 样例" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" "scripts\make_samples.py"

Write-Host "[5/5] 自检" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" "scripts\smoke_step1.py"
& ".venv\Scripts\python.exe" "scripts\smoke_step2.py"

Write-Host ""
Write-Host "完成。启动：.\scripts\run_dev.ps1" -ForegroundColor Green
