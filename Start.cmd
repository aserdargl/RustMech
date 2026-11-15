@echo off
if exist "%~dp0dist\RustMech\RustMech.exe" (
    start "" "%~dp0dist\RustMech\RustMech.exe" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-RustMech.ps1" %*
)
