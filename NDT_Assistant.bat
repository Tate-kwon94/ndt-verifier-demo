@echo off
REM ===========================================================================
REM NDT Assistant - GUI launcher (1-click, for non-technical users)
REM Usage: double-click this file. No console window; only the GUI opens.
REM (All user-facing text is Korean inside the GUI itself.)
REM ===========================================================================
setlocal
cd /d "%~dp0"

if not exist "%~dp0set_env.bat" (
    echo.
    echo  [!] Not installed yet.
    echo  Please double-click install.bat first to finish setup,
    echo  then run this file NDT_Assistant.bat again.
    echo.
    pause
    exit /b 1
)
call "%~dp0set_env.bat" >nul 2>&1
if not defined PYTHON set PYTHON=python
if not defined PYTHONUTF8 set PYTHONUTF8=1

REM pythonw.exe = GUI Python (no console window)
set PYW=%PYTHON%
if exist "%PYTHON:python.exe=pythonw.exe%" set PYW=%PYTHON:python.exe=pythonw.exe%

start "" "%PYW%" -m app.gui.launcher
endlocal
