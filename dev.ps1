# Memo App - One-click dev environment launcher
# Usage: .\dev.ps1 [-SkipConda] [-BackendOnly] [-FrontendOnly]
param(
    [switch]$SkipConda,
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$condaBase = "$env:USERPROFILE\AppData\Local\miniconda3"

# ========== 1. Detect Python ==========
$pythonPath = $null
if (-not $SkipConda) {
    # Prefer conda memo-env (check Scripts/python.exe for venv style)
    foreach ($candidate in @(
        "$condaBase\envs\memo-env\Scripts\python.exe",
        "$condaBase\envs\memo-env\python.exe"
    )) {
        if (Test-Path $candidate) {
            $pythonPath = $candidate
            Write-Host "[OK]  Conda env Python" -ForegroundColor Green
            Write-Host "      $(& $pythonPath --version)" -ForegroundColor DarkGray
            break
        }
    }
}

if (-not $pythonPath) {
    $pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $pythonPath) {
    Write-Error "Python not found. Install Miniconda or Python 3.11+"
    exit 1
}

# ========== 2. Check/Install deps ==========
$sitePkgs = Split-Path -Parent (Split-Path -Parent $pythonPath)
$sitePkgs = Join-Path $sitePkgs "Lib\site-packages"
if (-not (Test-Path (Join-Path $sitePkgs "fastapi"))) {
    Write-Host "[...] Installing Python dependencies..." -ForegroundColor Yellow
    & $pythonPath -m pip install -r "$root\backend\requirements.txt" -q 2>&1
    Write-Host "[OK]  Dependencies installed" -ForegroundColor Green
}

# ========== 3. Start Backend ==========
if (-not $FrontendOnly) {
    Write-Host "[...] Starting Python backend (http://127.0.0.1:8765)" -ForegroundColor Cyan
    $backendJob = Start-Process -FilePath $pythonPath -ArgumentList "$root\backend\main.py" -NoNewWindow -PassThru
    Start-Sleep -Seconds 2
}

# ========== 4. Start Frontend ==========
if (-not $BackendOnly) {
    if (Test-Path "$root\node_modules") {
        Write-Host "[...] Starting Electron dev server" -ForegroundColor Cyan
        npm --prefix "$root" run dev
    } else {
        Write-Warning "node_modules not found. Run: npm install"
    }
}
