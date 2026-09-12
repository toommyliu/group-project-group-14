@echo off
REM opengrader[wrapper]
setlocal
set "PSEXE=powershell"
where /q pwsh && set "PSEXE=pwsh"
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0opengrader.ps1" %*
exit /b %ERRORLEVEL%
