# ============================================================
#  停止本机实例（8010）
#  用法：右键 → 使用 PowerShell 运行
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'
$Port = 8010

$conn = Get-NetTCPConnection -LocalPort $Port -State Listen
if (-not $conn) { Write-Host "端口 $Port 无服务在运行" -ForegroundColor Green; exit 0 }

$pids = @($conn | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($procId in $pids) {
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p) { Write-Host "停止 PID=$procId $($p.ProcessName)（启动于 $($p.StartTime)）"; Stop-Process -Id $procId -Force }
}
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "端口 $Port 仍被占用，请在任务管理器里结束对应 python.exe" -ForegroundColor Red; exit 1
}
Write-Host "已停止，端口 $Port 已释放" -ForegroundColor Green
