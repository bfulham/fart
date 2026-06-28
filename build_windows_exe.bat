@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher "py" was not found.
    echo Install Python 3.10 or newer from python.org and enable the launcher.
    pause
    exit /b 1
)

py -3 -m pip install --upgrade pip
if errorlevel 1 goto :failed
py -3 -m pip install -r requirements-dev.txt
if errorlevel 1 goto :failed

py -3 -m unittest discover -s tests -v
if errorlevel 1 goto :failed

py -3 -m PyInstaller --noconfirm --clean FART.spec
if errorlevel 1 goto :failed

echo.
echo Built successfully: dist\FART.exe
pause
exit /b 0

:failed
echo.
echo Build failed. Review the error above.
pause
exit /b 1
