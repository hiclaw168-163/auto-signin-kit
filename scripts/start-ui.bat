@echo off
rem One-click launcher for the Web admin (SPEC-09). Double-click to run:
rem it builds the frontend first if missing (requires Node.js), then starts
rem the server and opens the browser. All branching lives inside the
rem auto-signin-kit ui commands (STD-06: scripts stay branch-free glue).
chcp 65001 >nul
auto-signin-kit ui build --if-missing
auto-signin-kit ui serve --open
pause
