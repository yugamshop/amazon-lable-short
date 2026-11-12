@echo off
cd /d "%~dp0"
title LabelDesk Local
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" "%~dp0run.py"
  goto done
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" bootstrap.py
  goto done
)
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.12 bootstrap.py
  goto done
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" bootstrap.py
  goto done
)
python -c "import sys" >nul 2>&1
if not errorlevel 1 (
  python bootstrap.py
  goto done
)
echo Install Python 3.12 64-bit from python.org, select Add Python to PATH, then run this file again.
pause
exit /b 1
:done
if errorlevel 1 pause
