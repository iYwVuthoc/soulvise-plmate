param(
    [switch]$ExeOnly,
    [switch]$InstallerOnly
)

$ErrorActionPreference = "Stop"

if ($ExeOnly -and $InstallerOnly) {
    throw "-ExeOnly and -InstallerOnly cannot be used together."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project Python was not found. Run scripts\setup.ps1 first."
}

$AppVersion = (& $PythonPath -c "from desktop_companion_agent import __version__; print(__version__)").Trim()
$Executable = Join-Path $ProjectRoot "work\deploy-output\SoulvisePlmate.exe"
$ReleaseExecutable = Join-Path $ProjectRoot "outputs\Soulvise-Plmate-v$AppVersion-Windows-x64.exe"

function Write-ReleaseManifest([string]$Version, [string]$OutputDirectory) {
    # Use exact names so a local rollback build is never listed in the current GitHub Release.
    $ExpectedNames = @(
        "Soulvise-Plmate-v$Version-Windows-x64.exe",
        "Soulvise-Plmate-Setup-v$Version-Windows-x64.exe"
    )
    $Files = @(
        foreach ($ExpectedName in $ExpectedNames) {
            $Candidate = Join-Path $OutputDirectory $ExpectedName
            if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
                Get-Item -LiteralPath $Candidate
            }
        }
    ) | Sort-Object Name
    if ($Files.Count -eq 0) {
        throw "No v$Version release files were found for SHA-256 generation."
    }

    $Lines = [System.Collections.Generic.List[string]]::new()
    foreach ($File in $Files) {
        $Hash = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash
        $Lines.Add("# $($File.Name)")
        $Lines.Add("# Size: $($File.Length) bytes")
        $Lines.Add("$Hash  $($File.Name)")
        $Lines.Add("")
    }
    $Manifest = Join-Path $OutputDirectory "SHA256SUMS.txt"
    $TemporaryManifest = Join-Path $OutputDirectory (".SHA256SUMS-" + [guid]::NewGuid().ToString("N") + ".tmp")
    [System.IO.File]::WriteAllLines(
        $TemporaryManifest,
        $Lines,
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $TemporaryManifest -Destination $Manifest -Force
    Write-Host "SHA-256 manifest updated for v$Version."
}

function Assert-FileUnlocked([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    try {
        $Stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $Stream.Dispose()
    } catch {
        throw "$Path is in use. Close the running application and try again."
    }
}

Set-Location -LiteralPath $ProjectRoot

if (-not $InstallerOnly) {
    $DeployTool = Join-Path $ProjectRoot ".venv\Scripts\pyside6-deploy.exe"
    if (-not (Test-Path -LiteralPath $DeployTool)) {
        throw "pyside6-deploy was not found. Run scripts\setup.ps1 first."
    }

    $PortableConfig = Join-Path $ProjectRoot "packaging\pysidedeploy.spec"
    $WorkDirectory = Join-Path $ProjectRoot "work"
    $DeployConfig = Join-Path $WorkDirectory "pysidedeploy.runtime.spec"
    New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
    Copy-Item -LiteralPath $PortableConfig -Destination $DeployConfig -Force
    Assert-FileUnlocked $Executable
    & $DeployTool --config-file $DeployConfig -f
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Executable)) {
        throw "Qt deployment did not produce work\deploy-output\SoulvisePlmate.exe."
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $ReleaseExecutable) -Force | Out-Null
    Assert-FileUnlocked $ReleaseExecutable
    Copy-Item -LiteralPath $Executable -Destination $ReleaseExecutable -Force
    Write-Host "Soulvise Plmate v$AppVersion EXE build completed."
}

if ($ExeOnly) {
    Write-ReleaseManifest -Version $AppVersion -OutputDirectory (Split-Path -Parent $ReleaseExecutable)
    Write-Host "EXE-only mode enabled; installer build was skipped."
    return
}

if (-not (Test-Path -LiteralPath $ReleaseExecutable)) {
    throw "$ReleaseExecutable was not found. Build the EXE first."
}

$ManifestPath = Join-Path $ProjectRoot "outputs\SHA256SUMS.txt"
if (Test-Path -LiteralPath $ManifestPath) {
    $ReleaseHash = (Get-FileHash -LiteralPath $ReleaseExecutable -Algorithm SHA256).Hash
    $ReleaseName = Split-Path -Leaf $ReleaseExecutable
    $Recorded = Select-String -LiteralPath $ManifestPath -Pattern "^[A-Fa-f0-9]{64}  $([regex]::Escape($ReleaseName))$" | Select-Object -First 1
    if ($null -ne $Recorded -and -not $Recorded.Line.StartsWith($ReleaseHash, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The EXE hash does not match the existing SHA256SUMS.txt entry."
    }
}

$InnoScript = Join-Path $ProjectRoot "installer\SoulvisePlmate.iss"
$IsccCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
$IsccPath = if ($null -ne $IsccCommand) { $IsccCommand.Source } else { $null }
if ($null -eq $IsccPath) {
    $Candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    $IsccPath = $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if ($null -eq $IsccPath) {
    throw "Inno Setup 6 was not found. Install it before building the installer."
}

& $IsccPath $InnoScript
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$Installer = Join-Path $ProjectRoot "outputs\Soulvise-Plmate-Setup-v$AppVersion-Windows-x64.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
    throw "Inno Setup did not produce the expected installer."
}
Write-ReleaseManifest -Version $AppVersion -OutputDirectory (Split-Path -Parent $ReleaseExecutable)
Write-Host "Soulvise Plmate v$AppVersion installer build completed."
