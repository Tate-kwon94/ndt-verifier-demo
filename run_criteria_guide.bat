@echo off
REM Generate inspection-criteria guide (HTML/Excel/Markdown)
REM Usage: run_criteria_guide.bat [discipline]
REM   default discipline: CP-P1
setlocal
chcp 65001 >nul 2>&1
call "%~dp0set_env.bat"
if not defined PYTHON set PYTHON=python
if not defined PYTHONUTF8 set PYTHONUTF8=1
cd /d "%~dp0"

set DISCIPLINE=%~1
if "%DISCIPLINE%"=="" set DISCIPLINE=CP-P1

"%PYTHON%" -m app.main criteria-guide --discipline %DISCIPLINE%
echo.
echo Result: open the newest .html in data\outputs\
pause
endlocal
