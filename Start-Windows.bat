@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ==================================================
echo           WWA ASO Checker - Windows Start
echo ==================================================
echo.

REM --------- 1) Find Python (py preferred on Win) ---------
set "PYEXE="

REM Try Windows Python launcher first
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYEXE=py -3"
) else (
  REM Try python
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PYEXE=python"
  ) else (
    REM Try python3
    where python3 >nul 2>nul
    if %errorlevel%==0 (
      set "PYEXE=python3"
    )
  )
)

if "%PYEXE%"=="" (
  echo [ERROR] Python not found on this PC.
  echo.
  echo To run WWA ASO Checker you need Python 3.10+ installed.
  echo 1^) Install Python from the official website:
  echo    https://www.python.org/downloads/windows/
  echo 2^) During install, ENABLE the checkbox:
  echo    "Add python.exe to PATH"
  echo 3^) Re-run this .bat file.
  echo.
  echo Opening Python download page...
  start "" "https://www.python.org/downloads/windows/"
  echo.
  pause
  exit /b 1
)

echo [INFO] Using: %PYEXE%
echo.

REM --------- 2) Create venv if missing ---------
if not exist "venv\Scripts\python.exe" (
  echo [INFO] Creating virtual environment (venv)...
  %PYEXE% -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    echo Possible причины:
    echo - Python installed without venv module
    echo - Permission issues
    echo.
    pause
    exit /b 1
  )
)

REM --------- 3) Use venv python/pip explicitly (more reliable) ---------
set "VPY=venv\Scripts\python.exe"

if not exist "%VPY%" (
  echo [ERROR] venv python not found at: %VPY%
  pause
  exit /b 1
)

echo [INFO] Upgrading pip...
"%VPY%" -m pip install --upgrade pip
echo.

REM --------- 4) Install dependencies ---------
if exist "requirements.txt" (
  echo [INFO] Installing dependencies from requirements.txt...
  "%VPY%" -m pip install -r requirements.txt
) else (
  echo [INFO] Installing dependencies (fallback list)...
  "%VPY%" -m pip install flask requests beautifulsoup4 lxml
)

if errorlevel 1 (
  echo.
  echo [ERROR] Dependency installation failed.
  echo Часті причини:
  echo - Antivirus blocked pip
  echo - Corporate proxy / no internet
  echo - SSL inspection issues
  echo.
  pause
  exit /b 1
)

echo.
echo [INFO] Starting server...
echo (When it starts, your browser should open automatically)
echo.

REM --------- 5) Run the app ---------
"%VPY%" app.py

echo.
echo [INFO] App process exited.
pause
