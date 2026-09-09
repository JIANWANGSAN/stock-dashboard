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
echo [1/2] fetch_data.py ...
"%PY%" fetch_data.py
echo.
echo [2/2] enrich_tags.py ...
"%PY%" enrich_tags.py
echo.
echo ============================================
echo   Done. Open index.html to view.
echo ============================================
pause
