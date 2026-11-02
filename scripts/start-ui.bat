@echo off
rem One-click launcher for the Web admin (SPEC-09/SPEC-10). Double-click to run:
rem it builds the frontend first if missing (requires Node.js), then starts
rem the server in the background (SPEC-10 daemon) and opens the browser.
rem All branching lives inside the auto-signin-kit ui commands (STD-06:
rem scripts stay branch-free glue). Stop with scripts/stop-ui.bat.
chcp 65001 >nul
auto-signin-kit ui build --if-missing
auto-signin-kit ui serve --daemon --open
pause
