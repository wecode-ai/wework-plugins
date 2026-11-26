# Keep CLI argv literal on Windows PowerShell 5.1.
# Authentication pages and confirmation are owned by the plugin.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
. (Join-Path $PSScriptRoot 'invoke-native-command.ps1')
$launchArguments = @($args)
if ($launchArguments.Count -eq 0) {
    $launchArguments = @('health')
}
$result = -1
Invoke-NativeCommand -Command {
    & node (Join-Path $PSScriptRoot 'mcp-launcher.mjs') @launchArguments
} -ExitCode ([ref]$result)
exit $result
