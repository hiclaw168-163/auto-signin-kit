#!/usr/bin/env bash
# 一键停止 Web 管理台后台实例(SPEC-10)。全部逻辑在 `ui stop` 内,
# 本脚本只是入口(STD-06 bash 红线: 无业务分支)。
set -euo pipefail
exec auto-signin-kit ui stop "$@"
