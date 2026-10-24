@echo off
rem Launcher: find a usable Python, then start the GUI.
rem Keep this file ASCII-only: cmd.exe mangles non-ASCII .cmd files on some systems.
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Resume Form Autofill

set "PY="
set "PYW="
call :find_python

if not defined PY goto :no_python

if "%NO_START%"=="1" goto :report

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 goto :no_tk

echo   Starting Resume Form Autofill ...
start "" %PYW% "%~dp0gui.py"
exit /b 0

:report
echo PY=%PY%
echo PYW=%PYW%
exit /b 0

:no_python
echo.
echo   [x] Python was not found on this computer.
echo.
echo   This tool needs Python 3.9+ ^(free^):
echo     1. download: https://www.python.org/downloads/windows/
echo     2. run the installer and CHECK "Add python.exe to PATH"
echo     3. then double-click this file again
echo.
echo   Opening the download page for you ...
start "" https://www.python.org/downloads/windows/
echo.
pause
exit /b 1

:no_tk
echo.
echo   [x] Python found, but tkinter is missing ^(needed for the window^).
echo       Re-run the Python installer, choose Modify, tick "tcl/tk and IDLE".
echo.
pause
exit /b 1

:find_python
where pythonw >nul 2>nul && set "PYW=pythonw"
where python  >nul 2>nul && set "PY=python"
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
if not defined PYW if defined PY set "PYW=%PY%"
if defined PYW (
  echo %PYW% | find /i "python.exe" >nul && call :exe_to_w
)
exit /b 0

:exe_to_w
set "PYW=%PYW:python.exe=pythonw.exe%"
exit /b 0
