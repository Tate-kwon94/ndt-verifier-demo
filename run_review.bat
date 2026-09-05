@echo off
REM Usage: run_review.bat <billing.xlsx> <reports.pdf> <round> <YYYY-MM-DD> <discipline>
REM e.g.: run_review.bat samples\billing\CP-M1_r2.xlsx samples\reports\CP-M1_r2.pdf 2 2026-06-30 CP-M1
setlocal
chcp 65001 >nul 2>&1
call "%~dp0set_env.bat"
if not defined PYTHON set PYTHON=python
if not defined PYTHONUTF8 set PYTHONUTF8=1
cd /d "%~dp0"

if "%~5"=="" (
    echo Usage: run_review.bat ^<billing.xlsx^> ^<reports.pdf^> ^<round^> ^<YYYY-MM-DD^> ^<discipline^>
    exit /b 1
)

"%PYTHON%" -m app.main review --billing "%~1" --reports "%~2" --round %~3 --date %~4 --discipline %~5
endlocal
