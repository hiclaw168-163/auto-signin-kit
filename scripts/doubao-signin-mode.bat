@echo off
rem Doubao (sign-in mode) launcher for Windows 11 - double-click / command line.
rem All logic lives in doubao-signin-mode.ps1; this file is only a thin wrapper
rem that forwards arguments verbatim (STD-06: scripts stay branch-free glue).
rem
rem Usage:
rem   doubao-signin-mode.bat                  # start/restart Doubao in sign-in mode
rem   doubao-signin-mode.bat -InstallShortcut # create Doubao-signin.lnk on Desktop
rem   doubao-signin-mode.bat -Port 9223 -Exe "D:\Doubao\app\Doubao.exe"
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0doubao-signin-mode.ps1" %*
if errorlevel 1 pause
