@echo off
rem Startar certifikatgeneratorn lokalt. Första gången (och när
rem requirements.txt har ändrats) installeras Python-beroendena.
chcp 65001 >nul
title Certifikatgenerator
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Förbereder första start, det tar någon minut ...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto :no_python
)

fc /b requirements.txt .venv\installed-requirements.txt >nul 2>&1
if errorlevel 1 (
    echo Installerar beroenden ...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 goto :install_failed
    copy /y requirements.txt .venv\installed-requirements.txt >nul
)

".venv\Scripts\python.exe" run_local.py
if errorlevel 1 pause
exit /b

:no_python
echo Python hittades inte. Installera Python 3 från https://www.python.org/downloads/
pause
exit /b 1

:install_failed
echo Installationen av beroenden misslyckades, se meddelandena ovan.
pause
exit /b 1
