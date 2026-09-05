@echo off
REM Usage: run_ingest_drawings.bat <drawings_folder> [YYYY-MM-DD]
setlocal
chcp 65001 >nul 2>&1
call "%~dp0set_env.bat"
if not defined PYTHON set PYTHON=python
if not defined PYTHONUTF8 set PYTHONUTF8=1
cd /d "%~dp0"

set FOLDER=%~1
set ASOF=%~2

if "%FOLDER%"=="" (
    echo Usage: run_ingest_drawings.bat ^<drawings_folder^> [YYYY-MM-DD]
    exit /b 1
)

if "%ASOF%"=="" (
    "%PYTHON%" -m app.main ingest-drawings "%FOLDER%"
) else (
    "%PYTHON%" -m app.main ingest-drawings "%FOLDER%" --as-of %ASOF%
)
endlocal
