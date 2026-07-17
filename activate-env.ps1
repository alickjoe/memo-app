# Memo App - Conda environment info script
# Usage: .\activate-env.ps1

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$condaBase = "$env:USERPROFILE\AppData\Local\miniconda3"

# Find Python in conda env (check venv-style first, then native)
$pythonPath = $null
foreach ($c in @(
    "$condaBase\envs\memo-env\Scripts\python.exe",
    "$condaBase\envs\memo-env\python.exe"
)) {
    if (Test-Path $c) { $pythonPath = $c; break }
}

if (-not $pythonPath) {
    Write-Warning "Environment 'memo-env' not found at:"
    Write-Warning "  $condaBase\envs\memo-env\"
    Write-Host ""
    Write-Host "Create it with one of:" -ForegroundColor Yellow
    Write-Host "  conda env create -f environment.yml" -ForegroundColor White
    Write-Host ""
    Write-Host "If conda repo is blocked (corp firewall):" -ForegroundColor DarkGray
    Write-Host "  conda create -n memo-env --clone base" -ForegroundColor White
    Write-Host "  conda activate memo-env" -ForegroundColor White
    Write-Host "  pip install -r backend/requirements.txt" -ForegroundColor White
    exit 1
}

Write-Host ""
Write-Host "=== Memo Conda Environment ===" -ForegroundColor Green
Write-Host "Python: $(& $pythonPath --version)" -ForegroundColor Cyan
Write-Host "Path  : $pythonPath" -ForegroundColor DarkGray
Write-Host ""

# Check dependencies
$sitePkgs = Split-Path -Parent (Split-Path -Parent $pythonPath)
$sitePkgs = Join-Path $sitePkgs "Lib\site-packages"
if (Test-Path (Join-Path $sitePkgs "fastapi")) {
    Write-Host "[OK]  Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "[WARN] Dependencies not installed. Run:" -ForegroundColor Yellow
    Write-Host "       $pythonPath -m pip install -r backend/requirements.txt" -ForegroundColor White
}

Write-Host ""
Write-Host "Commands:" -ForegroundColor DarkGray
Write-Host "  conda activate memo-env    - Activate environment" -ForegroundColor White
Write-Host "  python backend/main.py     - Start backend server" -ForegroundColor White
Write-Host "  npm run dev                - Start frontend dev" -ForegroundColor White
Write-Host "  .\dev.ps1                  - Start all at once" -ForegroundColor White
Write-Host ""
