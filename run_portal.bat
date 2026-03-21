@echo off
cd /d "%~dp0"
title MRL- Remote@T Management Hub
echo Starting MRL- Remote@T Broker...
start /b python web_server.py
timeout /t 3
echo Starting Local Agent...
start /b python ultimate_client.py --server localhost
echo ---
echo PORTAL LIVE AT: http://localhost:8000
echo ---
pause
