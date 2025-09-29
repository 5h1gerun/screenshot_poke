@echo off
setlocal
REM Build one-file EXE without typing PowerShell command
cd /d "%~dp0"
echo [Build] One-file EXE via scripts\build_release.ps1 -OneFile
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_release.ps1" -OneFile
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo Build failed with exit code %ERR%.
  pause
  exit /b %ERR%
)
echo Done. Output: dist\OBS-Screenshot-Tool.exe
pause

