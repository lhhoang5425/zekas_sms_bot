@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title ZEKAS SMS TELEGRAM BOT

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :python_error
)

".venv\Scripts\python.exe" -c "import aiogram, dotenv" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :install_error
)

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
".venv\Scripts\python.exe" src\bot.py
if errorlevel 1 pause
exit /b %errorlevel%

:python_error
echo Khong tim thay Python 3.
pause
exit /b 1

:install_error
echo Khong cai duoc thu vien. Hay kiem tra ket noi Internet.
pause
exit /b 1
