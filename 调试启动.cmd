@echo off
rem Same GUI, but keeps the console so you can see Python errors.
cd /d "%~dp0"
python "%~dp0gui.py"
if errorlevel 1 pause
