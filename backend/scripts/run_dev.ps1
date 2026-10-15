# 启动后端（默认 127.0.0.1:5000）
$ErrorActionPreference = "Stop"
chcp 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$backend = Split-Path -Parent $PSScriptRoot
Set-Location $backend

if (-not (Test-Path ".venv")) {
    Write-Host "还没有虚拟环境，先跑 .\scripts\install.ps1" -ForegroundColor Red
    exit 1
}

& ".venv\Scripts\python.exe" run.py
