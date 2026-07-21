$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Start logging to file (for debugging from NSIS installer)
$logFile = "$env:TEMP\memo-torch-install.log"
try { Stop-Transcript -ErrorAction SilentlyContinue } catch { }  # Stop any stale transcript
Start-Transcript -Path $logFile -Append -Force | Out-Null
Write-Host "=== Memo PyTorch Install Log ==="
Write-Host "Started at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ============================================================
# Step 0: Detect system Python (3.11-3.13)
# ============================================================
$useSystem = $false
$pythonExe = $null

$sysPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $sysPython) {
    $sysPython = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}

if ($sysPython) {
    $versionOutput = & $sysPython --version 2>&1
    if ($versionOutput -match "Python 3\.(\d+)") {
        $minor = [int]$Matches[1]
        if ($minor -ge 11 -and $minor -le 13) {
            $useSystem = $true
            $pythonExe = $sysPython
            Write-Host "Using system Python: $($versionOutput.Trim())"
        } else {
            Write-Host "System Python $($versionOutput.Trim()) is not in supported range (3.11-3.13), will use embeddable"
        }
    }
} else {
    Write-Host "No system Python found, will download embeddable Python 3.13"
}

# ============================================================
# Branch A: Install to system Python
# ============================================================
if ($useSystem) {
    try {
        Write-Host "Installing runtime dependencies..."
        & $pythonExe -m pip install --no-warn-script-location fastapi "uvicorn[standard]" soundcard numpy httpx aiosqlite
        if ($LASTEXITCODE -ne 0) { throw "pip install runtime deps failed" }

        Write-Host "Installing PyTorch..."
        & $pythonExe -m pip install --no-warn-script-location torch torchaudio --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple/
        if ($LASTEXITCODE -ne 0) { throw "pip install torch failed" }

        Write-Host "Verifying..."
        & $pythonExe -c "import fastapi, uvicorn, soundcard, numpy, httpx, aiosqlite, torch, torchaudio; print('OK')"
        if ($LASTEXITCODE -ne 0) { throw "import verification failed" }

        Write-Host "PyTorch installed to system Python successfully."
        Stop-Transcript | Out-Null
        exit 0
    } catch {
        Write-Host "ERROR: $_"
        Stop-Transcript | Out-Null
        exit 1
    }
}

# ============================================================
# Branch B: Download embeddable Python + install everything
# ============================================================
$targetDir = "$env:LOCALAPPDATA\Memo\python"
$pythonVersion = "3.13.1"
$pythonZip = "python-$pythonVersion-embed-amd64.zip"
$pythonUrl = "https://www.python.org/ftp/python/$pythonVersion/$pythonZip"

try {
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    # Download embeddable Python
    Write-Host "Downloading Python $pythonVersion..."
    Invoke-WebRequest -Uri $pythonUrl -OutFile "$env:TEMP\$pythonZip"

    # Extract
    Write-Host "Extracting Python..."
    Expand-Archive -Path "$env:TEMP\$pythonZip" -DestinationPath $targetDir -Force

    # Enable pip in embeddable Python
    $pthFile = Get-ChildItem -Path $targetDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        $content = Get-Content $pthFile.FullName
        $content = $content -replace "#import site", "import site"
        $content += ""
        $content += "Lib\site-packages"
        $content | Set-Content $pthFile.FullName
    }

    $pythonExe = "$targetDir\python.exe"

    # Install pip
    Write-Host "Installing pip..."
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile "$env:TEMP\get-pip.py"
    & $pythonExe "$env:TEMP\get-pip.py" --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }

    # Install runtime deps
    Write-Host "Installing runtime dependencies..."
    & $pythonExe -m pip install --no-warn-script-location fastapi "uvicorn[standard]" soundcard numpy httpx aiosqlite
    if ($LASTEXITCODE -ne 0) { throw "pip install runtime deps failed" }

    # Install PyTorch
    Write-Host "Installing PyTorch..."
    & $pythonExe -m pip install --no-warn-script-location torch torchaudio --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple/
    if ($LASTEXITCODE -ne 0) { throw "pip install torch failed" }

    # Verify
    Write-Host "Verifying..."
    & $pythonExe -c "import fastapi, uvicorn, soundcard, numpy, httpx, aiosqlite, torch, torchaudio; print('OK')"
    if ($LASTEXITCODE -ne 0) { throw "import verification failed" }

    Write-Host "PyTorch installed successfully."
} catch {
    Write-Host "ERROR: $_"
    Stop-Transcript | Out-Null
    exit 1
} finally {
    Remove-Item "$env:TEMP\$pythonZip" -ErrorAction SilentlyContinue
    Remove-Item "$env:TEMP\get-pip.py" -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
}
