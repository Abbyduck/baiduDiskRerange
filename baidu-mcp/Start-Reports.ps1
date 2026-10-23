param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'serve_reports.py') --port $Port
exit $LASTEXITCODE
