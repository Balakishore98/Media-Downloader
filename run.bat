@echo off
rem Launch MediaForge without a console window.
setlocal
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "MediaForge" pythonw app.py & exit /b)
python app.py
