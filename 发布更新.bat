@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"

set "PYEXE="
where python >nul 2>nul
if %errorlevel%==0 set "PYEXE=python"
if not defined PYEXE (
    if exist "E:\Houdini20.5.401\python311\python.exe" set "PYEXE=E:\Houdini20.5.401\python311\python.exe"
)
if not defined PYEXE (
    echo Cannot find Python. Please run manually:
    echo     python tools/release.py "what changed"
    pause
    exit /b 1
)

echo ============================================================
echo   Along's Houdini Toolkit - One-click Publish
echo ============================================================
echo.
echo   Steps: repack from N: drive  -^>  commit  -^>  push to GitHub
echo   1~2 minutes later, other machines can pull the update.
echo.
echo   Type "dry" and press Enter to just preview, no commit.
echo.
set /p MSG=What changed:

if "%MSG%"=="" set "MSG=chore: update toolkit"

if /i "%MSG%"=="dry" (
    "%PYEXE%" tools/release.py --dry-run
) else (
    "%PYEXE%" tools/release.py "%MSG%"
)

echo.
pause
