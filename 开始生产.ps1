# ============================================================
#   开始生产 —— 启动多作品生成流水线，并打开进度面板
#   已在运行就不会重复启动（避免两个进程抢同一个作品）
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root  = $PSScriptRoot
$Py    = Join-Path $Root '.venv\Scripts\python.exe'
$Panel = Join-Path $Root '进度面板.ps1'

# 已经在跑就不重复启动
$procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
           Where-Object { $_.CommandLine -like '*pipeline.py*' })

if ($procs.Count -gt 0) {
    Write-Host ''
    Write-Host '  生产线已经在运行了，直接打开进度面板。' -ForegroundColor Yellow
    Write-Host ''
    Start-Sleep -Seconds 2
} else {
    if (-not (Test-Path $Py)) {
        Write-Host ''
        Write-Host "  找不到 Python 环境：$Py" -ForegroundColor Red
        Write-Host '  请先检查 .venv 是否完整。' -ForegroundColor Red
        Write-Host ''
        Read-Host '  按回车退出'
        exit 1
    }

    Write-Host ''
    Write-Host '  正在启动生产流水线（窗口最小化运行）...' -ForegroundColor Green
    Start-Process -FilePath $Py `
                  -ArgumentList 'pipeline.py' `
                  -WorkingDirectory $Root `
                  -WindowStyle Minimized
    Start-Sleep -Seconds 3
}

& $Panel
