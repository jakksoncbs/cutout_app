@echo off
REM Double-click this (Windows) to start Cutout.
REM Leave this window open while you work; close it to quit.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required. Install it from https://www.python.org/downloads/
  echo Be sure to tick "Add Python to PATH" during install, then try again.
  pause
  exit /b 1
)
python launch.py
pause
