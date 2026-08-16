function Invoke-DwsCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [ValidateSet('silent', 'stdout', 'all')]
        [string]$OutputMode = 'silent'
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $output = $null
    try {
        # Windows PowerShell 5.1 turns native stderr into NativeCommandError.
        # DWS writes normal browser authorization progress to stderr, so native
        # commands must be judged by their exit code instead of that error record.
        $ErrorActionPreference = 'Continue'
        switch ($OutputMode) {
            'stdout' {
                $output = (& $Executable @Arguments 2>$null) -join "`n"
            }
            'all' {
                $output = (& $Executable @Arguments 2>&1) -join "`n"
            }
            default {
                & $Executable @Arguments *> $null
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    [PSCustomObject]@{
        ExitCode = $exitCode
        Output = $output
    }
}
