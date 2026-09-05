@echo off
REM Launch dashboard - http://localhost:8501
setlocal
chcp 65001 >nul 2>&1
call "%~dp0set_env.bat"
if not defined PYTHON set PYTHON=python
if not defined PYTHONUTF8 set PYTHONUTF8=1
cd /d "%~dp0"

"%PYTHON%" -m app.main dashboard
endlocal
