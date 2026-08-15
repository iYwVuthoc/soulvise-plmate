param(
    [Parameter(Mandatory = $true)]
    [string]$ExecutablePath,
    [Parameter(Mandatory = $true)]
    [string]$IsolatedAppData,
    [int]$DurationSeconds = 15
)

$ErrorActionPreference = "Stop"
$ResolvedExecutablePath = (Resolve-Path -LiteralPath $ExecutablePath).Path

# 同一路径若已有实例则停止验收，避免误关闭用户自己启动的程序。
$ExistingInstances = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $ResolvedExecutablePath })
if ($ExistingInstances.Count -ne 0) {
    throw "The executable is already running; close it before validation."
}

function Get-TestProcessIds([int]$RootProcessId) {
    # 沿父子关系收集本次启动的进程树，不依赖便携版或安装版的文件名。
    $ProcessIds = @($RootProcessId)
    do {
        $Added = $false
        $AllProcesses = Get-CimInstance Win32_Process
        foreach ($Candidate in $AllProcesses) {
            if (
                $Candidate.ParentProcessId -in $ProcessIds -and
                $Candidate.ProcessId -notin $ProcessIds
            ) {
                $ProcessIds += [int]$Candidate.ProcessId
                $Added = $true
            }
        }
    } while ($Added)
    return @($ProcessIds)
}

$NewDataDirectory = Join-Path $IsolatedAppData "Soulvise\SoulvisePlmate"
$LegacyDataDirectory = Join-Path $IsolatedAppData "Dongge\DonggeDesktopCompanionAgent"
$env:SOULVISE_PLMATE_DATA_DIR = $NewDataDirectory
$env:SOULVISE_PLMATE_LEGACY_DATA_DIR = $LegacyDataDirectory
$env:QT_QPA_PLATFORM = "offscreen"
$Process = $null
$RootProcessId = 0
$TestProcessIds = @()

try {
    $Process = Start-Process -FilePath $ResolvedExecutablePath -PassThru -WindowStyle Hidden
    $RootProcessId = $Process.Id
    Start-Sleep -Seconds $DurationSeconds

    $RootProcess = Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue
    if ($null -eq $RootProcess) {
        throw "SoulvisePlmate did not remain alive for the required duration."
    }
    $TestProcessIds = @(Get-TestProcessIds -RootProcessId $RootProcessId)
    $ActiveProcesses = @(Get-Process -Id $TestProcessIds -ErrorAction SilentlyContinue)

    $ExpectedFiles = @(
        "config.json",
        "contexts\supervision_context.md",
        "contexts\companion_context.md",
        "logs\migration-test.log",
        ".legacy-migration-v0.1.2.json"
    )

    foreach ($RelativePath in $ExpectedFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $NewDataDirectory $RelativePath))) {
            throw "Missing migrated file: $RelativePath"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $LegacyDataDirectory "config.json"))) {
        throw "Legacy data was unexpectedly removed."
    }

    $LegacyConfig = Join-Path $LegacyDataDirectory "config.json"
    $NewConfig = Join-Path $NewDataDirectory "config.json"
    $LegacyHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $LegacyConfig).Hash
    $NewHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $NewConfig).Hash
    if ($LegacyHash -ne $NewHash) {
        throw "Migrated config hash mismatch."
    }

    [pscustomobject]@{
        ParentPid = $RootProcessId
        ActiveProcessCount = $ActiveProcesses.Count
        NewDataDirectory = $NewDataDirectory
        LegacyDataPreserved = $true
        ConfigHashMatched = $true
    } | Format-List
}
finally {
    $TestInstances = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $ResolvedExecutablePath })
    foreach ($TestInstance in $TestInstances) {
        Stop-Process -Id $TestInstance.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Milliseconds 500
$RemainingProcesses = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $ResolvedExecutablePath })
if ($RemainingProcesses.Count -ne 0) {
    throw "Smoke-test processes remain alive."
}
Write-Output "SMOKE_PROCESS_CLEANUP_OK"
