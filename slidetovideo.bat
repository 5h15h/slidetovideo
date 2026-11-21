@echo off
REM Windows: double-click this.
cd /d "%~dp0"
python slidetovideo.py --app %*
if errorlevel 1 pause
