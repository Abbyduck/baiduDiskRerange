$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
Write-Host 'Paste the Baidu Access Token or the complete authorization result URL.'
Write-Host 'Input is hidden. The token will be encrypted for your Windows account.'
$secureInput = Read-Host 'Access Token' -AsSecureString
$credential = New-Object System.Management.Automation.PSCredential('baidu-mcp', $secureInput)
$plainInput = $credential.GetNetworkCredential().Password.Trim()
if ($plainInput -match '(?:[?#&]|^)access_token=([^&#\s]+)') {
    $plainInput = [Uri]::UnescapeDataString($Matches[1])
}
if ([string]::IsNullOrWhiteSpace($plainInput) -or $plainInput -match '\s|://|[?#&]') {
    throw 'Invalid token. Copy the Access Token or the full authorization result URL.'
}
$secureToken = ConvertTo-SecureString $plainInput -AsPlainText -Force
$secureToken | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'token.dpapi') -Encoding Ascii
$plainInput = $null
$credential = $null
Write-Host 'Token saved locally with Windows encryption. Checking the MCP connection...'
& (Join-Path $PSScriptRoot 'Start-BaiduMcp.ps1') -Probe
exit $LASTEXITCODE
