@echo off
cd /d "%~dp0"
echo ============================================
echo   A-share Dashboard - First time setup
echo   (Only needed once on a NEW computer)
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python is NOT installed.
    echo.
    echo Please install Python 3.10+ from https://www.python.org
    echo IMPORTANT: check "Add Python to PATH" during install.
    echo Then run this script again.
    echo.
    pause
    exit /b 1
)

echo [1/3] Found Python:
python --version
echo.

if exist ".venv\Scripts\python.exe" (
    echo [2/3] .venv already exists, skip creating.
) else (
    echo [2/3] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [X] Failed to create venv.
        pause
        exit /b 1
    )
)

echo.
echo [3/3] Installing pypinyin (needed for stock name abbreviation) ...
".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pypinyin
if errorlevel 1 (
    echo [!] Tsinghua mirror failed, trying default source ...
    ".venv\Scripts\python.exe" -m pip install pypinyin
)

echo.
echo ============================================
echo   Setup done!
echo   Next: double-click refresh.bat to fetch data
echo ============================================
pause
