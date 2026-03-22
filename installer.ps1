$url = "https://web-production-d6db5.up.railway.app/api/agent.exe"
$dest = "$env:TEMP\MRL_Agent.exe"

Write-Host "MRL- Remote@T | Node Deployment" -ForegroundColor Cyan
Write-Host "Downloading agent executable..."

try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    Write-Host "Agent downloaded successfully to $dest" -ForegroundColor Green
} catch {
    Write-Host "Failed to download agent EXE: $_" -ForegroundColor Red
    return
}

Write-Host "Initializing Remote Node..." -ForegroundColor Yellow

try {
    Start-Process $dest -WindowStyle Hidden
    Write-Host "Agent started successfully in the background." -ForegroundColor Green
} catch {
    Write-Host "Failed to start agent: $_" -ForegroundColor Red
}

Write-Host "Installation Complete. You can close this window." -ForegroundColor Cyan
