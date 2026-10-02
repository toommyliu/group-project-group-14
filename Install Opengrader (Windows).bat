@echo off
rem Double-click installer for Windows: runs packaging\install.ps1 from
rem wherever this folder was extracted, so nobody has to open a terminal.
setlocal

if not exist "%~dp0packaging\install.ps1" (
    echo.
    echo Opengrader's files were not found next to this installer.
    echo If you opened it from inside the ZIP file, extract the ZIP first
    echo ^(right-click it and choose "Extract All"^), then open this file
    echo from the extracted folder.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\install.ps1" %*
set "status=%ERRORLEVEL%"

if not "%status%"=="0" (
    echo.
    echo Opengrader was not installed. The message above says why.
)

echo.
pause
exit /b %status%
