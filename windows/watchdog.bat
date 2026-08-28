@echo off
title TG Power Suite - Watchdog
echo ===================================================
echo   TG Power Suite - Auto-Restart Watchdog (Windows)
echo ===================================================

cd /d "%~dp0\.."

:loop
echo [%date% %time%] Starting TG Power Suite...
python backend\run.py start --tray
echo [%date% %time%] Application stopped or crashed! Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
