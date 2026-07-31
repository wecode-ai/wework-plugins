$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$wecom = & (Join-Path $scriptDirectory 'install-wecom-cli.ps1') -PrintPath |
    Select-Object -Last 1

foreach ($name in @('WECOM_ACCESS_TOKEN', 'WECOM_BOT_ID', 'WECOM_SECRET')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

& $wecom contact --help *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'WeCom CLI has no usable local robot configuration. Opening QR authorization...'
    & $wecom init --noninteractive
    if ($LASTEXITCODE -ne 0) {
        throw 'WeCom QR authorization failed.'
    }
}

& $wecom contact --help *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'WeCom QR authorization did not produce a usable local configuration.'
}
Write-Host 'WeCom CLI is installed and locally configured.'

