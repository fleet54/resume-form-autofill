@echo off
rem Environment check. ASCII-only on purpose (cmd.exe + non-ASCII .cmd is fragile).
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Resume Form Autofill - Environment Check

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
  )
)
if not defined PY (
  for /d %%D in ("C:\Python3*") do (
    if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
  )
)

if not defined PY (
  echo.
  echo   [x] Python not found. Install it first:
  echo       https://www.python.org/downloads/windows/
  echo       remember to check "Add python.exe to PATH"
  echo.
  start "" https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

%PY% "%~dp0_env_check.py"
echo.
pause
