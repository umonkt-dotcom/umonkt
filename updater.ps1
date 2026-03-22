
$processId = 34412
Write-Host "[OTA] Waiting for process $processId to exit..."
while (Get-Process -Id $processId -ErrorAction SilentlyContinue) { Start-Sleep -Seconds 1 }
Write-Host "[OTA] Process exited. Replacing binary..."
$retry = 0
while ($retry -lt 5) {
    try {
        Move-Item -Path 'mrl_agent.new' -Destination 'C:\Users\MRL\AppData\Local\Python\pythoncore-3.14-64\python.exe' -Force -ErrorAction Stop
        Write-Host "[OTA] Replacement successful."
        break
    } catch {
        $retry++
        Write-Host "[OTA] File locked, retrying ($retry/5)..."
        Start-Sleep -Seconds 2
    }
}
Start-Process 'C:\Users\MRL\AppData\Local\Python\pythoncore-3.14-64\python.exe'
