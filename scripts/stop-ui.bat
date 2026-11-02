@echo off
rem One-click stopper for the Web admin background instance (SPEC-10).
rem All logic lives inside `ui stop` (STD-06: scripts stay branch-free glue).
chcp 65001 >nul
auto-signin-kit ui stop
pause
