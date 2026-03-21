@echo off
cd /d "%~dp0"
title MRL- Remote@T: Expert Cloud Deployer
echo ---
echo Preparing MRL- Remote@T for Global Launch...
echo ---
git init
git add .
git commit -m "Initial MRL- Remote@T Release"
git branch -M main
set /p repo_url="Paste your GitHub URL (e.g., https://github.com/umonkt-dotcom/umonkt.git): "
git remote add origin %repo_url%
echo ---
echo Syncing and Uploading...
echo ---
git push -f origin main
echo ---
echo MISSION SUCCESS! Check Railway to connect your GitHub.
pause
