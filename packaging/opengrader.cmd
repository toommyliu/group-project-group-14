@echo off
REM opengrader[wrapper] shim — makes `opengrader` a real command on Windows.
REM
REM PowerShell scripts are not executable by name: .PS1 is not in PATHEXT, so a
REM bare `opengrader` finds nothing even with the .ps1 sitting on PATH beside
REM it. This .cmd is what the shell actually finds and run, from cmd.exe and
REM from PowerShell alike; it does nothing but hand off to opengrader.ps1 in
REM its own directory (%~dp0), so the pair can be copied anywhere together.
REM
REM -NoProfile keeps a slow or noisy user profile out of every invocation.
REM -ExecutionPolicy Bypass applies to this process only — it changes no
REM machine setting — and is what stops the default RemoteSigned policy from
REM refusing a script that was copied rather than signed.
setlocal
set "PSEXE=powershell"
where /q pwsh && set "PSEXE=pwsh"
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0opengrader.ps1" %*
exit /b %ERRORLEVEL%
