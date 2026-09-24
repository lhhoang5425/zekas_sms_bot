@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title CONG CU GUI THONG BAO - ZEKAS SMS BOT

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :python_error
)

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
".venv\Scripts\python.exe" src\broadcast_ui.py
exit /b %errorlevel%

:python_error
echo Khong tim thay Python 3.
pause
exit /b 1
