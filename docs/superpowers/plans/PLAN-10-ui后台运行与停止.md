# PLAN-10: ui 后台运行与停止

> 对应规格: SPEC-10 · 状态: 待实施

变更记录:

- 2026-11-02: 初稿(执行前写,正文冻结)。

## Goal

一句话: `ui serve` 支持 `--daemon` 后台运行,新增 `ui stop` / `ui status`,
配 `scripts/stop-ui.*` 胶水,mac + Windows 双平台。

## Architecture

技术要点与取舍(详见 SPEC-10 §2):

- 守护用 **spawn 自身子进程**: `--daemon` 只负责 Popen 拉起 `--daemon-child`
  (POSIX `start_new_session=True`,Windows `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`),
  stdio 重定向进 `default_log_dir()/ui-serve.log`;daemon 编排与 uvicorn 解耦,可单测
- **PID 文件由子进程自己写**(消竞态),JSON 形态,放 `default_data_dir()/ui-serve.pid`
- 存活检测 `os.kill(pid,0)` / Windows ctypes;`ps` cmdline 匹配防 PID 复用误杀
- 所有停止逻辑进 `ui stop` 命令(受门禁),bash/bat 只做一行 exec(STD-06)

## 任务清单

Files + 步骤(顺序即实施顺序):

1. `src/auto_signin_kit/ui/daemon.py`(新增)
   - `pid_file()/log_file()/read_pid()/write_pid()/clear_pid()/is_alive()/matches_cmdline()/spawn()`
   - Windows 存活检测 ctypes 化;入口解析 `sys.argv[0]` → `shutil.which` 兜底
2. `src/auto_signin_kit/ui/cli.py`(改)
   - `serve` 加 `--daemon` 与 hidden `--daemon-child`;child 写/清 PID + 复用现有 open 逻辑
   - 新增 `stop`、`status`;异常映射沿用 `_run` + `ConfigError` 模式
3. `scripts/start-ui.sh` / `start-ui.bat`(改): 尾行 `serve --daemon --open`
4. `scripts/stop-ui.sh` / `stop-ui.bat`(新增): 一行 `exec auto-signin-kit ui stop`
5. `tests/ui/test_daemon.py`(新增)+ cli 分支测试(SPEC-10 §5 清单)
6. 文档同步: SPEC-09 变更记录加一行;SPEC-10 状态改"已实现";specs/README.md
   与 plans/README.md 索引;GUIDE-01 若有启动章节则补 stop 说明

## 验收

- 门禁: `uv run ruff check && uv run lint-imports && uv run pytest -q` 全绿
- 端到端(SPEC-10 §5): `start-ui.sh` → 浏览器出页面 → `ui status` 显示运行中
  (PID/端口/日志路径)→ `stop-ui.sh` → `ui status` 显示已停止;重复 `stop` 幂等;
  `--daemon` 二次启动被显式拒绝
- 手动跑一次真实命令,不只看测试绿(AGENTS.md 第 6 条)
