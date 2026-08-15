$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Project environment not found. Run scripts\setup.ps1 first."
}

Set-Location -LiteralPath $ProjectRoot
& $VenvPython -m ruff check src tests scripts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython .\scripts\check_open_source.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython -m pytest
exit $LASTEXITCODE
