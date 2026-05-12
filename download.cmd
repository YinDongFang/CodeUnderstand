@echo off
REM ASCII-only wrapper: cmd.exe uses system ANSI (e.g. GBK) for .cmd; UTF-8 breaks parsing.
if "%~1"=="" (
  echo Usage: %~nx0 ^<GitHub zip url^>
  echo Example: %~nx0 https://github.com/org/repo/archive/refs/heads/main.zip
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_run.ps1" -ZipUrl "%~1"
exit /b %ERRORLEVEL%
