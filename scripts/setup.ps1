$ErrorActionPreference = "Stop"

# Keep the virtual environment inside this project.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $Python) {
        & py -3.12 -m venv (Join-Path $ProjectRoot ".venv")
    } else {
        $Python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $Python) {
            throw "Python was not found. Install Python 3.12 and enable Add Python to PATH."
        }
        $Version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($Version -ne "3.12") {
            throw "Detected Python $Version. This project requires Python 3.12."
        }
        & python -m venv (Join-Path $ProjectRoot ".venv")
    }
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e "$ProjectRoot[dev]"

Write-Host "Setup complete. Run: powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1"
