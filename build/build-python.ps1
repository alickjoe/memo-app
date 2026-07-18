# Build Python Backend with PyInstaller
# Usage: .\build\build-python.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== Building Python Backend ===" -ForegroundColor Cyan

# Ensure pip dependencies are installed
Write-Host "Installing Python dependencies..."
pip install -r backend/requirements.txt
pip install pyinstaller

# Clean previous build
$outputDir = "backend-dist"
if (Test-Path $outputDir) {
    Remove-Item -Recurse -Force $outputDir
}
# Also clean stale PyInstaller spec/work to avoid stale --add-data references
if (Test-Path build/pyinstaller-spec) {
    Remove-Item -Recurse -Force build/pyinstaller-spec
}
if (Test-Path build/pyinstaller-work) {
    Remove-Item -Recurse -Force build/pyinstaller-work
}
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

# Build with PyInstaller
Write-Host "Running PyInstaller..."
pyinstaller `
    --onefile `
    --name backend `
    --distpath $outputDir `
    --workpath build/pyinstaller-work `
    --specpath build/pyinstaller-spec `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols `
    --hidden-import uvicorn.protocols.http `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import aiosqlite `
    --hidden-import pyaudio `
    --hidden-import soundcard `
    --clean `
    --noconfirm `
    backend/main.py

# Remove build artifacts
Remove-Item -Recurse -Force build/pyinstaller-work -ErrorAction SilentlyContinue

Write-Host "=== Python backend built: $outputDir/backend.exe ===" -ForegroundColor Green
