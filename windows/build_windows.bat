@echo off
title TG Power Suite - Windows EXE Builder
echo ===================================================
echo   TG Power Suite - Standalone Windows Builder
echo ===================================================

cd /d "%~dp0\.."

echo [+] Installing PyInstaller...
pip install pyinstaller

echo [+] Building standalone executable...
pyinstaller --noconfirm --onedir --windowed ^
    --name "TG_Power_Suite" ^
    --icon "assets\tg-power-suite.ico" ^
    --add-data "frontend;frontend" ^
    --add-data "assets;assets" ^
    --hidden-import "engineio.async_drivers.asgi" ^
    --hidden-import "pystray" ^
    --hidden-import "uvicorn" ^
    --hidden-import "telethon" ^
    --hidden-import "cryptg" ^
    backend\run.py

echo.
echo ===================================================
echo   Build complete! Output in dist\TG_Power_Suite
echo ===================================================
pause
