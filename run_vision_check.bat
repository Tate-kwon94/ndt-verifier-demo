@echo off
REM ===========================================================================
REM NDT Assistant - Vision (image input) capability check
REM
REM Sends one generated test image to an open model on Studio XP and reports
REM whether the model can actually read text inside the picture.
REM
REM Double-click (no args) -> tests provider "gemma".
REM   run_vision_check.bat gptoss   -> tests gpt-oss (text-only; failure expected)
REM
REM Prereqs: hosts entry + firewall + NDT_STUDIO_TOKEN + providers block
REM          uncommented in config\hcx.yaml  (see the internal guide, section 6)
REM ===========================================================================
setlocal
chcp 65001 >nul 2>&1
call "%~dp0set_env.bat"
if not defined PYTHON set PYTHON=python
if not defined PYTHONUTF8 set PYTHONUTF8=1
cd /d "%~dp0"

set TARGET=%~1
if "%TARGET%"=="" set TARGET=gemma

"%PYTHON%" scripts\vision_check.py --provider %TARGET%
echo.
pause
endlocal
