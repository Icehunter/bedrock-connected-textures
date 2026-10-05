@echo off
if exist "%~dp0Convert-Java-Pack.ps1" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Convert-Java-Pack.ps1" %*
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0convert_java_pack.ps1" %*
)
set "conversion_exit=%ERRORLEVEL%"
echo.
pause
exit /b %conversion_exit%
