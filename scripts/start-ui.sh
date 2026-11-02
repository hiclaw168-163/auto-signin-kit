#!/usr/bin/env bash
# 一键启动 Web 管理台(SPEC-09)。前端没构建会先自动构建(需要 Node.js),
# 然后起服务并打开浏览器。构建/依赖判断全在 `ui build --if-missing` 与
# `ui serve --open` 内,本脚本只是入口(STD-06 bash 红线: 无业务分支)。
set -euo pipefail
auto-signin-kit ui build --if-missing
exec auto-signin-kit ui serve --open
