#!/bin/bash
# 豆包(签到模式)启动器: 带 CDP 调试端口启动豆包客户端。
#
# 背景: macOS 豆包的 Cookie 加密为魔改 os_crypt,auto-signin-kit 只能走
# CDP(--remote-debugging-port)读实时会话(见 domains/doubao/cookies.py
# 模块注释)。日常用本启动器代替直接打开豆包即可,登录态不受影响。
#
# 行为:
#   - 豆包未运行            → 直接带调试端口拉起;
#   - 豆包在普通模式运行    → 完全退出后带调试端口拉起(登录态在 profile 里,不丢);
#   - 豆包已带调试端口运行  → 仅把豆包带到前台,不动它。
#
# 端口须与 config/domains/doubao.yaml 的 cdp_port 一致(默认 9223)。
set -u

PORT="${DOUBAO_CDP_PORT:-9223}"
BROWSER="/Applications/Doubao.app/Contents/Helpers/Doubao Browser.app/Contents/MacOS/Doubao Browser"

if ! [ -x "$BROWSER" ]; then
  osascript -e 'display alert "豆包(签到模式)" message "未找到豆包客户端,请先安装 Doubao.app" '
  exit 1
fi

# 已带调试端口在跑: 激活窗口即可(open 对运行中的应用只做前台激活,不重启)
if curl -s --max-time 1 "http://127.0.0.1:$PORT/json/version" | grep -q Browser; then
  open -a "Doubao"
  exit 0
fi

# 普通模式在跑: 完全退出再拉起。实测豆包对 AppleScript quit 不总是响应
# (签到模式引导启动的实例尤其如此),等 10 秒没退干净就对主进程升级 SIGTERM
# (Chromium 对 SIGTERM 走正常关停,登录态照常落盘)。
if pgrep -f "Doubao Browser.app" > /dev/null 2>&1; then
  osascript -e 'quit app "Doubao"' 2> /dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "Doubao Browser.app" > /dev/null 2>&1 || break
    sleep 1
  done
  if pgrep -f "Doubao Browser.app" > /dev/null 2>&1; then
    pkill -TERM -f "/Applications/Doubao.app/Contents/MacOS/Doubao" 2> /dev/null
    pkill -TERM -f "Doubao Browser.app/Contents/MacOS/Doubao Browser" 2> /dev/null
    sleep 2
  fi
fi

# 带调试端口拉起(会引导主程序以签到模式运行);脱离终端会话,双击不挂窗口
nohup "$BROWSER" --remote-debugging-port="$PORT" > /dev/null 2>&1 &
exit 0
