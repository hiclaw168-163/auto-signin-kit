@echo off
rem 一键启动 Web 管理台(SPEC-09)。双击即可: 前端没构建会先自动构建(需要
rem Node.js),然后起服务并打开浏览器。构建/依赖等所有判断逻辑都在
rem auto-signin-kit ui 命令内部(STD-06 红线: 脚本只做无分支的纯调用)。
chcp 65001 >nul
auto-signin-kit ui build --if-missing
auto-signin-kit ui serve --open
pause
