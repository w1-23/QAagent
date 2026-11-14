# CitrusSchalor complete local startup: Windows app + WSL Docker drawing.
param(
    [int]$Port = 8010,
    [string]$Python = 'E:\anaconda\python.exe',
    [string]$Distribution = 'Ubuntu-24.04'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Windows Python not found: $Python"
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $Repo
try {
    & $Python -B -X utf8 (Join-Path $PSScriptRoot 'local_startup.py') --port $Port --distribution $Distribution
    $Code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $Code
