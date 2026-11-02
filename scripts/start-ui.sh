#!/usr/bin/env bash
# 一键启动 Web 管理台(SPEC-09/SPEC-10)。前端没构建会先自动构建(需要
# Node.js),然后后台拉起服务并打开浏览器,脚本立即退出。构建/依赖/守护
# 判断全在 `ui build --if-missing` 与 `ui serve --daemon` 内,本脚本只是
# 入口(STD-06 bash 红线: 无业务分支)。关闭用 scripts/stop-ui.sh。
set -euo pipefail
auto-signin-kit ui build --if-missing
exec auto-signin-kit ui serve --daemon --open
