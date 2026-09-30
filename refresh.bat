@echo off
cd /d "%~dp0"

REM ---- 自动探测 Python：优先项目内 .venv，其次系统 python，最后原固定路径 ----
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    if exist "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
        set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    )
)
if not defined PY (
    echo [X] Python not found. Please run setup.bat first.
    pause
    exit /b 1
)

echo ============================================
echo   Refresh A-share dashboard data
echo   Python: %PY%
echo ============================================
echo.

REM ---- dependency check: missing pypinyin silently blanks ALL stock-name
REM      abbreviations in data.json (real incident on 2026-09-22) ----
"%PY%" -c "import pypinyin" 2>nul
if errorlevel 1 (
    echo [X] pypinyin NOT installed -- stock abbreviations would be EMPTY.
    echo     Installing it now ...
    "%PY%" -m pip install pypinyin
    "%PY%" -c "import pypinyin" 2>nul
    if errorlevel 1 (
        echo [X] Still missing. Run manually:  ^<python^> -m pip install pypinyin
        pause
        exit /b 1
    )
)
echo [1/3] fetch_data.py ...（已内置 macro.py：盘后一并刷新「必看」页宏观面板）
"%PY%" fetch_data.py
echo.
echo [2/3] enrich_tags.py ...
"%PY%" enrich_tags.py
echo.
echo [3/3] module4.py ...
"%PY%" module4.py
echo.
echo ============================================
echo   Done. Open index.html to view.
echo ============================================
pause
