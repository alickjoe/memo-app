# Memo App - One-click dev environment launcher (venv)
# Usage: .\dev.ps1 [-BackendOnly] [-FrontendOnly]
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = "$root\.venv\Scripts\python.exe"

# ========== 1. Ensure venv exists ==========
if (-not (Test-Path $venvPython)) {
    Write-Host "[...] Creating Python venv..." -ForegroundColor Yellow
    $systemPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $systemPython) {
        Write-Error "Python not found. Please install Python 3.11+ and add to PATH."
        exit 1
    }
    & $systemPython -m venv "$root\.venv" 2>&1
    if (-not (Test-Path $venvPython)) {
        Write-Error "Failed to create venv."
        exit 1
    }
    Write-Host "[OK]  Venv created" -ForegroundColor Green
}

# ========== 2. Install/verify dependencies ==========
$sitePkgs = "$root\.venv\Lib\site-packages"
if (-not (Test-Path (Join-Path $sitePkgs "fastapi"))) {
    Write-Host "[...] Installing Python dependencies..." -ForegroundColor Yellow
    & $venvPython -m pip install -r "$root\backend\requirements.txt" -q 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install dependencies. Check network and try again."
        exit 1
    }
    Write-Host "[OK]  Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "[OK]  Dependencies already installed" -ForegroundColor Green
}

Write-Host "[OK]  Python: $(& $venvPython --version)" -ForegroundColor DarkGray

# ========== 3. Start Backend ==========
if (-not $FrontendOnly) {
    Write-Host "[...] Starting Python backend (http://127.0.0.1:8765)" -ForegroundColor Cyan
    $env:BACKEND_PORT = "8765"
    Start-Process -FilePath $venvPython -ArgumentList "$root\backend\main.py" -NoNewWindow
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
