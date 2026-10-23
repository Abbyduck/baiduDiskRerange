param([switch]$Probe)
$ErrorActionPreference = 'Stop'
$nodePath = 'C:\Program Files\nodejs\node.exe'
$bridgePath = Join-Path $PSScriptRoot 'bridge.mjs'
if ($Probe) { & $nodePath $bridgePath --probe }
else { & $nodePath $bridgePath }
exit $LASTEXITCODE
