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
REM NOTE: the Tsinghua mirror is NOT reachable on some machines (returns
REM       "No matching distribution found") -> try the OFFICIAL PyPI first.
".venv\Scripts\python.exe" -m pip install pypinyin
if errorlevel 1 (
    echo [!] Default PyPI failed, trying Tsinghua mirror ...
    ".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pypinyin
)

echo.
echo [check] Verifying pypinyin is importable ...
".venv\Scripts\python.exe" -c "import pypinyin;print('  pypinyin OK ->',pypinyin.__file__)"
if errorlevel 1 (
    echo.
    echo [X] pypinyin STILL missing. Stock abbreviations will be EMPTY.
    echo     Install it manually into the interpreter that runs the scripts:
    echo         ^<python^> -m pip install pypinyin
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Setup done!
echo   Next: double-click refresh.bat to fetch data
echo ============================================
pause
