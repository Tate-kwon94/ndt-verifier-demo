@echo off
REM Usage: run_ingest_standards.bat <folder> <scwep|code|contract>
setlocal
chcp 65001 >nul 2>&1
call "%~dp0set_env.bat"
if not defined PYTHON set PYTHON=python
if not defined PYTHONUTF8 set PYTHONUTF8=1
cd /d "%~dp0"

if "%~2"=="" (
    echo Usage: run_ingest_standards.bat ^<folder^> ^<scwep^|code^|contract^>
    exit /b 1
)

"%PYTHON%" -m app.main ingest-standards "%~1" --type %~2
endlocal
