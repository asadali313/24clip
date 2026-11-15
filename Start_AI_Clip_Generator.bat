@echo off
title ClipNova AI
rem Launcher for ClipNova AI - all data stays on D:
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.main
if errorlevel 1 (
    echo.
    echo The application exited with an error. Check D:\AI_Clip_Generator\logs
    pause
)
