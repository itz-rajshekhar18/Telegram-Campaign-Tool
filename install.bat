@echo off
title Telegram Campaign Tool - Backend Installer
echo ============================================
echo  Telegram Campaign Tool - Backend Installer
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
)

echo Activating and installing dependencies...
call venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo Creating .env file if it doesn't exist...
if not exist .env (
    copy .env.example .env
    echo.
    echo IMPORTANT: Edit the .env file and add your API_ID and API_HASH
    echo Get them from https://my.telegram.org
    echo.
)

echo.
echo ============================================
echo  Installation complete!
echo ============================================
echo.
echo Next steps:
echo 1. Edit .env and add your Telegram API_ID and API_HASH
echo 2. Run:  venv\Scripts\activate
echo 3. Run:  python login.py     (to connect your Telegram account)
echo 4. Run:  python -m app.main  (to start the backend)
echo 5. Edit static\miniapp.html and set your laptop IP
echo.
echo For full guide, open README.md
echo.
pause
