@echo off
setlocal
title AI Clip Generator - Setup
cd /d "%~dp0"
echo ============================================================
echo   AI Clip Generator - First-time Setup
echo   This prepares everything automatically on this PC.
echo ============================================================
echo.

rem ---- 1. Python check -----------------------------------------------------
set "PYEXE="
py -3 -c "import sys; print('OK' if sys.version_info >= (3,10) else 'OLD')" > "%TEMP%\aig_pyver.txt" 2>&1
findstr /C:"OK" "%TEMP%\aig_pyver.txt" >nul
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE (
    python -c "import sys; print('OK' if sys.version_info >= (3,10) else 'OLD')" > "%TEMP%\aig_pyver.txt" 2>&1
    findstr /C:"OK" "%TEMP%\aig_pyver.txt" >nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python 3.10 or newer 64-bit was not found.
    echo.
    echo Install it from:  https://www.python.org/downloads/windows/
    echo IMPORTANT: tick "Add python.exe to PATH" in the installer,
    echo then run this setup again.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PYEXE% -c "import sys; print(sys.version.split()[0])"') do set PYVER=%%v
echo [1/4] Python %PYVER% found.

rem ---- 2. Virtual environment ----------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [2/4] Virtual environment already exists - reusing it.
    goto venv_done
)
echo [2/4] Creating virtual environment...
%PYEXE% -m venv .venv
if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
)
:venv_done
set "VPY=.venv\Scripts\python.exe"

rem ---- 3. Dependencies -------------------------------------------------------
echo [3/4] Installing Python dependencies - 5 to 10 minutes on first run...
"%VPY%" -m pip install --upgrade pip -q
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. Check your internet connection.
    pause
    exit /b 1
)

rem ---- 4. FFmpeg --------------------------------------------------------------
if exist "bin\ffmpeg.exe" (
    echo [4/4] FFmpeg already present.
    goto ffmpeg_done
)
echo [4/4] Downloading FFmpeg - about 100 MB, one time...
if not exist "bin" mkdir "bin"
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile 'temp_ffmpeg.zip'"
if errorlevel 1 (
    echo [ERROR] FFmpeg download failed. Download it manually from
    echo https://github.com/BtbN/FFmpeg-Builds/releases
    echo and place ffmpeg.exe + ffprobe.exe into the bin folder.
    pause
    exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Path 'temp_ffmpeg.zip' -DestinationPath 'temp_ffmpeg' -Force"
for /r "temp_ffmpeg" %%f in (ffmpeg.exe ffprobe.exe) do copy /y "%%f" "bin\" >nul
rmdir /s /q "temp_ffmpeg"
del /q "temp_ffmpeg.zip" 2>nul
if not exist "bin\ffmpeg.exe" (
    echo [ERROR] FFmpeg was not extracted correctly.
    pause
    exit /b 1
)
:ffmpeg_done

echo.
echo ============================================================
echo   Setup complete! Launching AI Clip Generator...
echo   Next time just double-click Start_AI_Clip_Generator.bat
echo ============================================================
start "" ".venv\Scripts\pythonw.exe" -m app.main
timeout /t 3 >nul
exit /b 0
