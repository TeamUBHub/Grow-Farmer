param(
    [string]$BootstrapPath = ""
)

$ErrorActionPreference = "Stop"

$ManagerUrl = "https://raw.githubusercontent.com/YOURUSER/YOURREPO/main/manager.py"

$Root      = Join-Path $env:LOCALAPPDATA "RobloxManager"
$PyDir     = Join-Path $Root "python"
$PyExe     = Join-Path $PyDir "python.exe"
$ManagerPy = Join-Path $Root "manager.py"
$WsHost    = "127.0.0.1"
$WsPort    = 8765

function Write-Info {
    param([string]$msg)
    Write-Host "[i] $msg" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$msg)
    Write-Host "[+] $msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$msg)
    Write-Host "[!] $msg" -ForegroundColor Yellow
}

function Write-ErrMsg {
    param([string]$msg)
    Write-Host "[x] $msg" -ForegroundColor Red
}

function Test-ManagerRunning {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($WsHost, $WsPort, $null, $null)
        $connected = $iar.AsyncWaitHandle.WaitOne(500, $false)
        if ($connected -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Install-MicroPython {
    Write-Info "No local Python found - setting up an isolated copy in $PyDir ..."
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null

    $pyVersion = "3.12.4"
    $embedUrl  = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip"
    $zipPath   = Join-Path $env:TEMP "python-embed.zip"

    Write-Info "Downloading embeddable Python $pyVersion..."
    Invoke-WebRequest -Uri $embedUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $PyDir -Force
    Remove-Item $zipPath -Force

    $pthFile = Get-ChildItem -Path $PyDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        (Get-Content $pthFile.FullName) -replace '^#\s*import site', 'import site' |
            Set-Content $pthFile.FullName
    }

    Write-Info "Installing pip..."
    $getPipPath = Join-Path $env:TEMP "get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath -UseBasicParsing
    & $PyExe $getPipPath --no-warn-script-location | Out-Null
    Remove-Item $getPipPath -Force

    Write-Ok "Isolated Python environment ready."
}

function Ensure-DesktopShortcut {
    $shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Roblox Account Manager.lnk"
    if (Test-Path $shortcutPath) {
        return
    }

    $answer = Read-Host "Create a desktop shortcut for next time? (Y/n)"
    if ($answer -match '^[Nn]') {
        return
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)

    if ($BootstrapPath -and (Test-Path $BootstrapPath)) {
        $shortcut.TargetPath = $BootstrapPath
    } else {
        $persistedScript = Join-Path $Root "launch.ps1"
        Copy-Item -Path $PSCommandPath -Destination $persistedScript -Force
        $shortcut.TargetPath = "powershell.exe"
        $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$persistedScript`""
    }

    $shortcut.WorkingDirectory = $Root
    $shortcut.IconLocation = $PyExe
    $shortcut.Description = "Launch Roblox Account Manager"
    $shortcut.Save()

    Write-Ok "Desktop shortcut created."
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null

if (Test-ManagerRunning) {
    Write-Ok "Manager already running on ${WsHost}:${WsPort} - not starting a second instance."
    exit 0
}

Write-Info "Fetching latest manager.py..."
Invoke-WebRequest -Uri $ManagerUrl -OutFile $ManagerPy -UseBasicParsing

if (-not (Test-Path $PyExe)) {
    Install-MicroPython
} else {
    Write-Info "Using existing isolated Python at $PyDir"
}

Write-Info "Installing/updating required packages..."
$packages = @("selenium", "webdriver-manager", "colorama", "requests", "websockets")
& $PyExe -m pip install --upgrade --no-warn-script-location $packages | Out-Null
Write-Ok "Packages ready."

Ensure-DesktopShortcut

Write-Info "Launching manager..."
& $PyExe $ManagerPy
