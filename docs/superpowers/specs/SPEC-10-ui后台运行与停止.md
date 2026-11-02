# SPEC-10: ui 后台运行与停止(daemon / stop / status)

> 日期: 2026-11-02 · 状态: 待评审 · 关联: [SPEC-09](SPEC-09-Web管理台.md) · [STD-06](../../standards/STD-06-脚本与资产规范.md)

变更记录:

- 2026-11-02: 初稿。`ui serve` 此前只有前台阻塞模式,停止脚本无处安放;
  本 SPEC 补后台运行(`--daemon`)+ `ui stop` / `ui status`。

## 1. 定位与边界

给"管理台常驻本机"的场景补齐生命周期管理:

- `ui serve --daemon`:后台拉起服务,脚本/终端立即退出
- `ui stop`:停止后台实例(幂等)
- `ui status`:查运行状态与日志路径
- `scripts/stop-ui.sh`:与 `start-ui.sh` 配对的纯胶水入口

明确**不做**(YAGNI):

- 不加 `ui logs` 子命令——`status` 显示日志路径,用户自己 tail
- 不做开机自启/launchd/计划任务注册——那是 schedule 域的职责,不混
- 不做多实例管理——同时只允许一个服务实例
- 不做优雅排空——本机工具,uvicorn 无长连接状态需要保护

## 2. 已定架构决策

| 决策 | 结论 | 理由 |
|------|------|------|
| 守护方式 | **spawn 自身子进程**(方案 B) | `--daemon` 用 `subprocess.Popen` 重新拉起自己的 `--daemon-child` 隐藏子命令;跨平台只差 Popen flags,主干逻辑一份,daemon 编排可单测。备选的进程内 double-fork(POSIX/Windows 两套路径难测)与 launchd/计划任务(过重)被否 |
| PID 文件归属 | **子进程自己写**(启动即写 `os.getpid()`) | 父进程写有竞态:父写完子秒挂,PID 文件指向死进程 |
| 状态/日志位置 | PID 文件 `default_data_dir()/ui-serve.pid`(JSON);日志 `default_log_dir()/ui-serve.log` | 复用 [log_dir.py](../../../src/auto_signin_kit/core/adapters/log_dir.py) 现成路径收口;数据(可变状态)与日志分居的既有原则不变 |
| 平台范围 | macOS + Windows | 项目本就双平台交付;Windows 侧进程存活性检测走 ctypes,风格对齐 `core/adapters/win_process.py` |
| 脚本定位 | bash/bat 只做无分支入口 | STD-06 红线: 有业务分支的逻辑必须进 CLI 命令(受门禁),脚本只是一行 exec |

## 3. 组件设计

### 3.1 `ui/daemon.py`(新增;纯逻辑,禁止 import typer)

| 函数 | 行为 |
|------|------|
| `pid_file()` | `default_data_dir()/ui-serve.pid`,JSON 格式 `{"pid", "host", "port", "started_at"}`(`status` 直接取 host/port/started_at 展示) |
| `log_file()` | `default_log_dir()/ui-serve.log` |
| `spawn(host, port, open_browser)` | `Popen` 拉起 `ui serve --daemon-child --host ... --port ... [--open]`;stdio 全重定向到日志文件(`stdin=DEVNULL, stdout=日志, stderr=STDOUT`);POSIX `start_new_session=True`,Windows `creationflags=DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP`;可执行入口优先解析 `sys.argv[0]` 绝对路径,其次 `shutil.which("auto-signin-kit")`,都找不到 → `ConfigError` |
| `read_pid()` | 读 JSON;文件不存在/损坏/字段缺失 → 返回 None(由调用方按陈旧处理) |
| `write_pid(pid, host, port)` / `clear_pid()` | 原子写(临时文件 + replace)/删除 |
| `is_alive(pid)` | POSIX `os.kill(pid, 0)`,`PermissionError` 也算活;Windows ctypes `OpenProcess` + `GetExitCodeProcess == STILL_ACTIVE` |
| `matches_cmdline(pid)` | 尽力而为的进程身份校验:macOS/Linux `ps -p <pid> -o args=` 输出须含 `daemon-child`;Windows 直接返回 True(跳过)——防 PID 复用后误杀无关进程 |

### 3.2 `ui/cli.py` 改动(只做参数解析,逻辑在 daemon.py)

| 命令 | 行为 |
|------|------|
| `serve --daemon` | 已有实例存活(且 cmdline 匹配)→ `ConfigError` 退出码 1,提示先 `ui stop`,**不静默复用**;否则 `spawn` → 轮询最多 5s 确认子进程存活 → 打印 PID 与日志路径后正常退出 |
| `serve --daemon-child`(hidden) | 启动即 `write_pid(os.getpid())` → 复用 SPEC-09 的 `--open` 延迟打开逻辑 → `uvicorn.run(...)`;退出时清理 PID 文件 |
| `stop` | 见 §4 错误处理表;SIGTERM(Windows `TerminateProcess`)后轮询最多 10s,未退 SIGKILL 强杀;结束清 PID 文件 |
| `status` | 运行中(PID/host/port/started_at/日志路径)或已停止(含原因: 无文件/陈旧/损坏) |

### 3.3 脚本

- `scripts/start-ui.sh` / `start-ui.bat`:尾行改 `ui serve --daemon --open`(bat 等价),构建步骤 `ui build --if-missing` 不变
- `scripts/stop-ui.sh` / `stop-ui.bat`:各仅一行 `exec auto-signin-kit ui stop`

## 4. 错误处理(全部显式,不静默兜底)

| 场景 | 行为 |
|------|------|
| `--daemon` 时已有实例在跑 | `ConfigError` 退出码 1,提示 `ui stop` |
| `spawn` 本身失败(OSError) | 显式报错,不写 PID 文件(本来也是子进程写) |
| 子进程 5s 内退出(典型: 端口被占) | 父进程报错并指向日志文件路径 |
| PID 文件损坏/非法 JSON | `stop`/`status` 视为已停止,提示可手动删文件;`stop` 退出码 0 |
| 陈旧 PID(进程已死) | 清理 PID 文件,提示"上次未正常退出";`stop` 退出码 0 |
| PID 复用(cmdline 不匹配) | 不杀,按陈旧处理并提示 |
| `stop` 无 PID 文件 | 提示未在运行,退出码 0(幂等) |
| Windows 停止失败(权限等) | 显式报错,**保留** PID 文件(下次可再试) |

## 5. 测试策略

- **单元测试** `tests/ui/test_daemon.py`(绝不真起 uvicorn):
  - PID 文件: 读写 round-trip / 损坏 JSON / 字段缺失 / 原子写;monkeypatch 路径到 `tmp_path`
  - `is_alive`: `Popen(sleep)` 验活态,`wait()` 后验死态(两平台同构,无需 skipif)
  - `spawn`: fake `Popen` 记录参数,断言 `start_new_session`/`creationflags` 按平台正确、stdio 重定向到日志文件
  - `matches_cmdline`: fake `subprocess.run` 返回值两分支
- **CLI 测试**(CliRunner): `stop`/`status` 全分支(无文件/陈旧/损坏/运行中 mock);`--daemon` 已有实例报错(spawn 打 mock)
- **端到端手动验收**(AGENTS.md 门禁节教训: 测试绿 ≠ 看起来对): `start-ui.sh` → 浏览器出页面 → `ui status` → `stop-ui.sh` → `ui status` 显示已停止;mac 实测,Windows 行为由单元测试覆盖
- **门禁**: `uv run ruff check && uv run lint-imports && uv run pytest -q` 全绿

## 6. 架构与约束核对

- `daemon.py` 在 `ui/` 层内,不 import typer(CLI 只做解析),不 import domains——import-linter 现有 `ui → cli → domains → orchestration → core` 契约不变
- 新增逻辑全部受门禁保护;`daemon.py` 与 `cli.py` 行数远低于 410 上限
- SPEC-09 的"只绑 127.0.0.1"边界不变,后台化不引入对外暴露

## 7. 实现顺序

见 [PLAN-10](../plans/PLAN-10-ui后台运行与停止.md)(一个 SPEC 一个 PLAN,编号对齐)。
