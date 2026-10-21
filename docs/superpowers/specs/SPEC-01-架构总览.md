# SPEC-01-架构总览

> **概要**: auto_signin_kit 的整体架构——分层、组合根、依赖规则、数据流、错误体系;
> 这里回答"整个项目是怎么组织起来的"。
> **状态**: 已实现(与代码同步维护;代码变了这里必须跟着变)
> **关联**: [SPEC-02-core层公共契约](SPEC-02-core层公共契约.md) ·
> [SPEC-03-workbuddy域](SPEC-03-workbuddy域.md) ·
> [SPEC-04-qoder域](SPEC-04-qoder域.md) · [SPEC-05-trae域](SPEC-05-trae域.md)

## 1. 一句话

`auto-signin-kit` 是**多域 CLI 工具箱**: 每个签到目标(WorkBuddy / Qoder CN / Trae CN)
是一个独立"域",经 entry_points 挂进统一 CLI;域只自带三件套
(`schema.py` + `service.py` + `cli.py`),共享的底层能力(请求/报告/日志/凭据解密)
全部下沉到 `core/`。

## 2. 分层

```text
┌────────────────────────────────────────────────────────────┐
│ 组合根 cli.py      只做 entry_points 发现,不 import 任何域    │
├────────────────────────────────────────────────────────────┤
│ domains/<域>/      每域固定三件套 + 域内私有模块(api/auth/…)  │
│   凭据解锁 → 请求 → 业务映射 → 一行 JSON                        │
├────────────────────────────────────────────────────────────┤
│ orchestration/     Pipeline(顺序执行 + 逆序回滚),多步骤命令用   │
├────────────────────────────────────────────────────────────┤
│ core/              httpclient / report / logging / errors /    │
│   config / prompt / envvar / adapters(OS 差异只许在这)          │
└────────────────────────────────────────────────────────────┘
依赖方向: cli → domains → core;core 不反向依赖任何上层。
域与域之间互不 import。
```

为什么这样分——问答式关键决策(细节见对应 ADR):

- **Q: 域怎么注册,为什么"永远不手工 add_typer"?**
  A: pyproject `[project.entry-points."auto_signin_kit.domains"]`,
  `cli.py`(组合根)在运行时枚举挂载([ADR-0001](../adr/ADR-0001-entry-points组合根.md)).
- **Q: 为什么 service.py 禁止 `import typer`?**
  A: 纯逻辑层才可被单测 mock 全覆盖;交互走 `core.prompt` 注入
  (contextvars,`cli` 层 `set_prompt`,测试兜底 `input`)。
  这是本架构里唯一**复发过不止一次**的 bug 类型,已被门禁测试拦截。
- **Q: OS 差异写哪?** A: 只许写进 `core/adapters/`
  (`platform_info` 判平台、`win_dpapi` 凭据、`log_dir` 目录定位…)。
  唯一例外: 组合根统一 reconfigure Windows 控制台编码。
- **Q: 什么时候建 core/ports/ 端口?**
  A: 只有"这类依赖将来真会出现第二种实现"才建;目前每个外部依赖都只有
  一种实现,全部直接实现([ADR-0002](../adr/ADR-0002-端口采用判据.md))。
- **Q: 请求层为什么带时间预算?**
  A: 计划任务的 ExecutionTimeLimit 会硬杀进程——预算用尽宁可如实返回
  `TIMEOUT`,也要让日志写得出来([ADR-0003](../adr/ADR-0003-时间预算优先于无限重试.md))。
- **Q: 结果为什么双通道?** A: `silent` 结果写日志文件;`ERROR` 结果即使
  参数拼错也会落盘,失败无痕是真实发生过的坑(见 SPEC-02 §report)。

## 3. 组合根与域注册

`src/auto_signin_kit/cli.py`:

- `build_app()` 枚举 entry_points(group=`auto_signin_kit.domains`),
  按名字排序 `add_typer`;一个都没有 → `RuntimeError`(提示
  `uv sync && uv tool install --editable . --force`)。
- **坑(文档必须背书)**: entry_points 物化在 dist-info 里,只改 pyproject.toml
  不会自动生效,必须重装才能看到新域。
- `main()`: Windows 下控制台 reconfigure UTF-8;`AutoSignInKitError` 按其
  `exit_code` 退出;裸 `RuntimeError` → 退出码 2。

## 4. 依赖规则(全部机器强制)

### import-linter(pyproject,`uv run lint-imports`)

| 契约 | 规则 |
|------|------|
| 总体分层(layers) | `cli > domains > orchestration > core`,只能向下依赖 |
| core 不依赖 domains | forbidden |
| domains 之间彼此独立(independence) | workbuddy / qoder / trae 互不 import;真有跨域需求须显式登记 `ignore_imports` |
| ports 不依赖 adapters | 已预写、注释着——建了 `core/ports/` 才打开(ADR-0002) |

### tests/test_architecture_gate.py(纯 Python 门禁,9 条)

1. `test_domains_have_no_platform_branches` — OS 判定(`sys.platform` 等)不得出现在 domains/。
2. `test_domains_do_not_call_subprocess_directly` — 子进程统一走 core 封装。
3. `test_core_does_not_import_domains` — core 反向依赖即报。
4. `test_ports_do_not_import_adapters` — 建了 ports 才生效。
5. `test_source_file_line_limit` — 单文件 ≤ 410 行(逼近上限主动拆)。
6. `test_cli_is_pure_composition_root` — cli.py 不 import 任何域。
7. `test_every_domain_dir_registered_via_entry_points` — 域目录必须在 pyproject 注册。
8. `test_service_no_raw_input` — service 禁 `input()`。
9. `test_service_modules_do_not_import_typer` — service 混入 typer 即报。

原则: 沉淀新约束先问"能不能变成门禁测试"——能就改这里,不能才写文档。

## 5. 一次运行的数据流(以 `trae auto --silent` 为例)

```text
typer(cli.py) → load_domain_config_model("trae", TraeDomainConfig, merge_local=True)
            → service.run_action("claim", config, silent=True)
                ├ Budget(TRAE_BUDGET_SECONDS 解析,非法回落,越界夹上限)
                ├ build_api(config): TRAE_TOKEN 优先,否则 auth 解本地登录态
                ├ Api.claim/status → core.httpclient.call(预算守卫+分桶退避)
                ├ result 映射(CLAIMED/ALREADY/AUTH_REJECTED/…)
                └ emit_result(silent→平台日志目录 trae.log;
                              ERROR 双通道;令牌 [REDACTED])
            → 退出码 0 / 1 / 2
```

外围兜底: 域 cli 的 `_run` 只包 `AutoSignInKitError → typer.Exit(exit_code)`;
service 的 `run_action` 自身保证**不抛业务异常**(本地异常收拢为 NO_AUTH/AUTH_ERROR
结果)。

## 6. 错误与退出码

| 退出码 | 含义 | 来源 |
|--------|------|------|
| 0 | 成功(含幂等成功 ALREADY/DRY_RUN/INACTIVE) | service run_action |
| 1 | 业务失败(网络/认证/领取失败) | `AutoSignInKitError.exit_code=1` 或 result≠成功 |
| 2 | 缺凭据/配置(NO_AUTH/配置文件缺失) | `ConfigError`/`NoAuthError`/service |
| 3 | 命令参数/流程错误 | `CommandError` |
| 4 | 适配层失败(域必须转译后上抛) | `AdapterError` |

分层异常: `AdapterError`(core/adapters)→ 域层转译为域内 Auth 错误
→ `run_action` 收拢成 result → `emit_result`。

## 7. 目录地图(带职责)

```text
src/auto_signin_kit/
├── cli.py                     # 组合根(上述)
├── orchestration/             # pipeline.py + step.py(Pipeline/Step/StepResult)
├── core/                      # 详见 SPEC-02
│   ├── envvar / errors / httpclient / logging / report / prompt
│   ├── ports/(scheduler: 定时调度端口,只放形状)
│   ├── config/(惰性配置解析 + pydantic schema)
│   └── adapters/(platform_info/native_helper/log_dir/win_dpapi/win_registry/win_process/client_discovery/scheduler)
└── domains/
    ├── workbuddy/  13 文件  → SPEC-03
    ├── qoder/       7 文件  → SPEC-04
    ├── trae/        6 文件  → SPEC-05
    └── schedule/    4 文件  → SPEC-06
config/domains/*.yaml (+ *.local.yaml 本机覆盖,不入库)
config/examples/*.yaml.example
tests/  (core / domains/{workbuddy,qoder,trae} / test_architecture_gate.py)
docs/standards/(STD-01..06 规范) docs/superpowers/specs/(本目录) docs/adr/
```

## 8. 失差同步费(regression 责任)

改动触碰以下内容时必须同步对应 SPEC,否则读者会拿着过期地图撞墙:

| 改动 | 要同步 |
|------|--------|
| 新增/删除域命令、参数 | 对应 SPEC-NN 域 + README 用法 |
| result 值词典/退出码语义 | SPEC-02 表格 |
| 凭据解锁法变化(端上升级) | 对应 SPEC-NN + 逆向笔记 |
| 新增门禁规则 | SPEC-01 §4 |
| 配置字段增删(额外形态) | 对应 SPEC-NN + docs/standards README 索引 |
