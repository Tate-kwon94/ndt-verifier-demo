@echo off
REM ===========================================================================
REM NDT Assistant - offline installer (full Python installer method)
REM
REM Bundle layout (inside the import zip):
REM   installer\python\python-3.11.x-amd64.exe   (full installer: tkinter + pip)
REM   installer\wheels\*.whl                      (all deps, per requirements-win.lock)
REM   installer\tesseract\tesseract.exe + tessdata\  (portable OCR: eng/rus/kor)
REM
REM Run: install.bat  (no admin rights needed; per-user install)
REM Result: Python 3.11 (only if missing) + offline deps + set_env.bat
REM ===========================================================================
setlocal enabledelayedexpansion
set ROOT=%~dp0
cd /d "%ROOT%"
chcp 65001 >nul 2>&1

REM -- 1) Detect Python 3.11 (py launcher -> PATH python -> default per-user path)
set PYEXE=
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3.11 -c "import sys;print(sys.executable)"') do set PYEXE=%%P
)
if not defined PYEXE (
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)"') do set PYEXE=%%P
    )
)
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
)

REM -- 2) If missing, install bundled Python (silent, per-user, PATH, tcl/tk)
if not defined PYEXE (
    set PYSETUP=
    for %%F in ("%ROOT%installer\python\python-3.11*-amd64.exe") do set PYSETUP=%%~fF
    if not defined PYSETUP (
        echo [ERROR] Python 3.11 not found, and no installer in installer\python\.
        pause
        exit /b 1
    )
    echo [INFO] Installing Python 3.11 - per-user, approx 1-2 min...
    "!PYSETUP!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1 Include_test=0
    if errorlevel 1 (
        echo [ERROR] Auto-install failed. Run the installer in installer\python\ manually, then re-run install.bat.
        pause
        exit /b 1
    )
    set PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
)
if not exist "!PYEXE!" (
    echo [ERROR] python.exe not found: !PYEXE!
    pause
    exit /b 1
)
echo [INFO] Python: !PYEXE!

REM -- 3) Offline dependency install (full Python ships pip; no bootstrap needed)
set REQ=%ROOT%requirements.txt
if exist "%ROOT%requirements-win.txt" set REQ=%ROOT%requirements-win.txt
echo [INFO] Upgrading pip offline...
"!PYEXE!" -m pip install --no-index --find-links "%ROOT%installer\wheels" -q --upgrade pip
echo [INFO] Installing dependencies: !REQ!
"!PYEXE!" -m pip install --no-index --find-links "%ROOT%installer\wheels" -r "!REQ!"
if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check installer\wheels\ against requirements-win.lock.
    pause
    exit /b 1
)

REM -- 4) Write env script (UTF-8 + OCR paths)
> "%ROOT%set_env.bat" (
    echo @echo off
    echo set PYTHON=!PYEXE!
    echo set PYTHONUTF8=1
    echo set NDT_TESSERACT_CMD=%ROOT%installer\tesseract\tesseract.exe
    echo set NDT_TESSDATA_PREFIX=%ROOT%installer\tesseract\tessdata
)

REM -- 5) Install self-check (import smoke, incl. tkinter)
"!PYEXE!" -c "import yaml, openpyxl, httpx, typer, PIL, pdfplumber, pypdfium2, tkinter"
if errorlevel 1 (
    echo [ERROR] Smoke test failed - check installer\wheels or the Python install.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Install complete. Next:
echo   NDT_Assistant.bat        - GUI, double-click
echo   run_review.bat / run_dashboard.bat
echo.
pause
endlocal
