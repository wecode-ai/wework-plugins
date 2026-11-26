function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,

        [Parameter(Mandatory = $true)]
        [ref]$ExitCode,

        [switch]$DiscardOutput
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ExitCode.Value = -1
    try {
        # Windows PowerShell 5.1 converts native stderr into non-terminating
        # ErrorRecord objects. Native commands must be judged by their exit
        # code, including commands that print normal OAuth progress to stderr.
        $ErrorActionPreference = 'Continue'
        if ($DiscardOutput) {
            & $Command *> $null
        } else {
            & $Command
        }
        if ($null -ne $LASTEXITCODE) {
            $ExitCode.Value = [int]$LASTEXITCODE
        }
    } catch {
        if (-not $DiscardOutput) {
            Write-Error -ErrorRecord $_ -ErrorAction Continue
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}
