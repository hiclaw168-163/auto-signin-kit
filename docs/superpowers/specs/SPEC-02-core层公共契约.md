# SPEC-02-core层公共契约

> **概要**: core/ 各模块的 as-built 契约——所有域共享的请求、报告、日志、
> 配置、错误、prompt 与平台适配层。读完应能不看源码判断"某契约是谁的职责"。
> **状态**: 已实现
> **关联**: [SPEC-01](SPEC-01-架构总览.md) · 各域 SPEC-03/04/05

## 目录

envvar · logging(含 log_dir 适配器) · report · errors · httpclient ·
config · prompt · orchestration · adapters。

## 1. `core/envvar.py` — 全局环境变量前缀

`auto_signin_kit_env(name)` 读 `AUTO_SIGNIN_KIT_<name>`,空/未设返回 None。
目前两个用户: `LOG_LEVEL`、`CONFIG_DIR`。域自己的环境变量
(`WORKBUDDY_*` / `QODER_*` / `TRAE_*`)对齐原项目命名,不走这个前缀。

## 2. `core/logging.py` + `adapters/log_dir.py` — 过程日志与结果落盘

- 过程日志(`log_debug/info/warning/error/success`)**全部走 stderr**,
  级别由 `AUTO_SIGNIN_KIT_LOG_LEVEL` 控制(默认 INFO)。
  12-Factor 式"日志即事件流": 业务输出走 stdout,`eval`/命令替换不被污染。
- **结果落盘只有一个入口**: `result_log_path(domain, env_var=…, filename=…)`。
  优先级: 自定义路径(`env_var`,如 `TRAE_SIGNIN_LOG`)>
  平台日志目录下 `filename`。平台差异收口在 `adapters/log_dir.py`:

  | 平台 | 目录 |
  |------|------|
  | Windows | `%LOCALAPPDATA%\auto-signin-kit\logs` |
  | macOS | `~/Library/Logs/auto-signin-kit` |
  | Linux | XDG State(`~/.local/state/auto-signin-kit/logs`) |

  硬规则: 日志目录由 `log_dir.default_log_dir()` 统一定位——
  `AUTO_SIGNIN_KIT_LOG_DIR` > 项目 `logs/` 子目录(可编辑安装时即仓库根,
  `logs/` 已 gitignore;直接写仓库**根目录**的旧方案已清理)> 平台目录。
- 落盘文件名统一按域名派生: `<域名>.log`(workbuddy.log / qoder.log /
  trae.log),由 `result_log_path` 统一命名,域层不自起文件名。

## 3. `core/report.py` — JSON Lines 事件契约(emit)

`emit_result(out, *, domain, action, silent, log_path, sensitive, quiet,
log_errors_without_silent)`——域 service 层唯一出口,本函数保证不抛异常。
两个通道输出**同一条 JSON Lines 记录**(整行完整 JSON,可 `jq` 过滤),
标准字段: `ts`(ISO 8601 带时区)、`level`(`ERROR`=needs_attention,其余
`INFO`)、`domain`/`action`(来源域与命令,参数或 out 同名字段,都没有则省略)。

| 参数/行为 | 语义 |
|-----------|------|
| stdout 通道 | 非 silent 时写该记录;`sys.stdout` 为 None(pythonw)则静默跳过 |
| 落盘通道 | 追加写同一 JSON 对象(时间戳在 `ts` 字段内,不再拼行首);写不进去回退 stdout(至少不丢结果) |
| `ERROR` 双通道 | `result=="ERROR"` 时两条通道都走——即便 silent 参数写错也有痕迹 |
| `log_errors_without_silent` | workbuddy 语义 True;**qoder/trae 传 False**(非静默不再回写) |
| `sensitive` | 序列化后把已知令牌统一替换 `[REDACTED]`(域层登记,见 api.SENSITIVE) |
| `quiet` | True 时跳过落盘;域用"空跑不落盘"契约: ALREADY/INACTIVE 不写,
  强制开关 `*_SIGNIN_LOG_EMPTY` |
| `needs_attention` | 缺省时按 `result in ATTENTION_RESULTS` 自动补:

ATTENTION_RESULTS(需人工介入): `ERROR, UNKNOWN, NETWORK, TIMEOUT,
NO_AUTH, NO_SESSION, AUTH_ERROR, AUTH_REJECTED, FORBIDDEN, PENDING, INACTIVE_ERROR`。

## 4. `core/errors.py` — 异常→退出码

| 异常 | 退出码 | 用途 |
|------|--------|------|
| `AutoSignInKitError` | 1 | 业务基类;cli 捕获后按 `exit_code` 退出 |
| `ConfigError` | 2 | 配置缺失/非法(extra=forbid、目录不存在、字段类型错) |
| `CommandError` | 3 | 命令参数/流程错误;PipelineError 继承它 |
| `AdapterError` | 4 | core/adapters 失败基类;**域不得直接上抛**,要转译 |

## 5. `core/httpclient.py` — 请求层(stdlib,零第三方)

契约核心: `call()`/`fetch()` 返回 `(status, body, err)` 三元组,**永不抛异常**。

| status | 含义 |
|--------|------|
| `NO_NETWORK`(0) | 没拿到响应: 连不上/超时;`err` 带原因 |
| `BUDGET_OUT`(-2) | 没发出去: 时间预算耗尽,body= {"error": …} |
| HTTP 状态码 | 原样;JSON 解析失败时 body=`{"raw": 截断原文}` |

- `Budget(seconds)`: 单调时钟,不受系统改时间影响;`call()` 前置检查
  `remaining()<=1` 直接 BUDGET_OUT,每次重试前也查预算。
- `RetryPolicy` **按失败类型分桶**(网络 / 服务端 5xx / 特定状态码 429),
  桶内各记退避进度——冷启动时前几次是网络不可达、之后才转 5xx,
  一个全局计数会让 5xx 一次都重试不到(workbuddy 实测经验)。
- `payload` 规则: bytes/bytearray 原样发送(POST 空体 `b""`);dict/list 走
  JSON 序列化;序列化失败显式报 `PAYLOAD_NOT_SERIALIZABLE`(曾修复的坑)。
- 写操作是否重试由调用方传入: 只有幂等接口才开重试。

## 6. `core/config/` — 域 YAML 唯一读取入口

- `resolve_config_dir()` **惰性函数**(禁止缓存成模块级常量——曾真实踩坑:
  常量冻结后测试重定向 env 失效)。优先级: `AUTO_SIGNIN_KIT_CONFIG_DIR` >
  包内 `auto_signin_kit/config`(wheel) > 仓库根 `config/`(editable)。
- `load_domain_config_model(domain, schema_cls, merge_local=True)`:
  读 `config/domains/<域>.yaml` → (可选)同目录 `<域>.local.yaml` **深合并**
  (映射递归合并、标量覆盖;local 不入库) → pydantic 校验。
- schema 基类 `DomainConfig` 用 `extra="forbid"`: 多余字段/拼错键**立即报错**,
  不静默忽略。

## 7. `core/prompt.py` — 交互注入

service 层禁止 import typer;需要交互时调用 `prompt_str/prompt_confirm`,
背后由组合根 `set_prompt()` 注入 `typer.prompt` 包装(contextvars)。
未注入(单测)兜底 builtins.input。

## 8. `orchestration/` — Pipeline

`Pipeline(steps)` 顺序执行;任一步失败 → **逆序回滚**已完成步骤
(回滚失败仅 log_warning,不掩盖原异常)→ 抛 `PipelineError(正确失败步骤, completed)`;
兼容包装旧 typer 命令(`SystemExit(0)` 视为成功)。多步骤、需要失败回滚的
命令一律用它,不写大函数手动兜底。

## 9. `core/adapters/` — OS/客户端差异层

| 适配器 | 职责 |
|--------|------|
| `platform_info` | IS_WINDOWS/IS_MACOS/IS_LINUX 等平台判定**唯一合法处** |
| `win_dpapi` | DPAPI `unprotect` + AES-256-GCM(`decrypt_gcm`,cryptography 优先/CNG 兜底) + AES-CBC(`encrypt_cbc/decrypt_cbc`,同策略) |
| `log_dir` | 平台日志目录(见 §2) |
| `native_helper` | `run_json_stdin_helper`(JSON-stdin 凭据助手调用)/`run_capture` 短命令捕获;domains 禁 subprocess 的唯一替代品 |
| `win_registry` | 卸载注册表项只读扫描 |
| `win_process` | 运行进程 exe 发现 |
| `client_discovery` | 桌面客户端安装定位(注册表/默认目录/mac bundle) |
| `scheduler/` | 定时调度三平台实现(见 SPEC-06): `windows`(schtasks)/`darwin`(launchd)/`linux`(cron),工厂 `get_scheduler()` 按 OS 分派 |

注: `run_json_stdin_helper` 的 JSON-stdin 协议 + 子进程输入输出管并发限制,
是给"必须借用客户端运行时"的场景(workbuddy sym-v1 解密)。

### 9.1 `core/ports/` — 端口层

`core/ports/scheduler.py` 只放形状(`Scheduler` Protocol、`ScheduleSpec`、
`TaskStatus`),不 import 任何 adapter——由 import-linter 的
`ports 不依赖 adapters` 契约强制。目前只有定时调度一个端口: 它确实存在三种
真实实现。没有第二种实现的 OS 差异直接进 `core/adapters/`,不在这里加层。

## 10. 全局环境变量速查

| 变量 | 作用 |
|------|------|
| `AUTO_SIGNIN_KIT_LOG_LEVEL` | 过程日志级别(默认 INFO) |
| `AUTO_SIGNIN_KIT_CONFIG_DIR` | 配置目录覆盖(必须已存在) |
| 域级变量 | 见 SPEC-03/04/05,"环境变量"小节 |
