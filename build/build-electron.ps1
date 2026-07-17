# Build Electron App
# Usage: .\build\build-electron.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== Building Electron App ===" -ForegroundColor Cyan

# Install Node dependencies
Write-Host "Installing Node dependencies..."
npm ci

# Build React frontend + Electron main process
Write-Host "Building frontend..."
npm run build

# Build Python backend first
Write-Host "Building Python backend..."
.\build\build-python.ps1

# Package with electron-builder
Write-Host "Running electron-builder..."
npx electron-builder --win --x64

# Output location
$version = (Get-Content package.json | ConvertFrom-Json).version
Write-Host "=== Installer built: release/Memo-Setup-$version.exe ===" -ForegroundColor Green
