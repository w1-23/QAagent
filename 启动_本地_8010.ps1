# ============================================================
#  启动本机实例（http://127.0.0.1:8010）
#  用途：开发/验收用。代码 = 本仓库 01_源码_src，数据 = agent\data（新语料）
#  用法：右键 → 使用 PowerShell 运行；或  powershell -ExecutionPolicy Bypass -File 启动_本地_8010.ps1
#  说明：后台启动，关掉这个窗口服务不会停；停止请运行 停止_本地_8010.ps1
# ============================================================
$ErrorActionPreference = 'Stop'

$Root = 'E:\codex_WORKSPACES\Citrus_QA_Agent'
$Py   = 'E:\anaconda\envs\rag-agent\python.exe'
$Port = 8010

if (-not (Test-Path $Py))   { Write-Host "找不到 Python: $Py" -ForegroundColor Red; exit 1 }
if (-not (Test-Path (Join-Path $Root '01_源码_src\src\api\main.py'))) { Write-Host "找不到代码: $Root\01_源码_src" -ForegroundColor Red; exit 1 }

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "端口 $Port 已被占用（PID $($busy.OwningProcess -join ',')）。先运行 停止_本地_8010.ps1" -ForegroundColor Yellow
    exit 1
}

# 运行期约定：PYTHONUTF8 必开（否则 GBK 控制台打印中文会崩）；NO_PROXY 必开（本机代理会拦 127.0.0.1）
$env:PYTHONUTF8     = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH     = Join-Path $Root '01_源码_src'
$env:NO_PROXY       = '*'

$LogDir = Join-Path $Root 'agent\logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out   = Join-Path $LogDir "local_${Port}_$stamp.log"
$err   = "$out.err"

$p = Start-Process -FilePath $Py `
     -ArgumentList '-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port',"$Port",'--workers','1' `
     -WorkingDirectory (Join-Path $Root '01_源码_src') `
     -RedirectStandardOutput $out -RedirectStandardError $err `
     -PassThru -WindowStyle Hidden

Write-Host "已启动 PID $($p.Id)" -ForegroundColor Green
Write-Host "  日志: $out"
Write-Host "  等待就绪（首次加载检索模型约 60-70 秒）..." -ForegroundColor DarkGray

for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    if ($p.HasExited) { Write-Host "进程已退出，看日志: $err" -ForegroundColor Red; Get-Content $err -Tail 15 -Encoding UTF8; exit 1 }
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$Port/" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) {
            Write-Host "就绪：http://127.0.0.1:$Port/  （$((($i+1)*2)) 秒）" -ForegroundColor Green
            exit 0
        }
    } catch { }
}
Write-Host "超时未就绪，看日志: $out" -ForegroundColor Yellow
exit 1
