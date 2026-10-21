# auto-signin-kit

各编程助手(WorkBuddy / CodeBuddy 系、Qoder CN、Trae CN)每日签到、领奖的
自动化工具箱。用 `polydomain-cli` 的多域 CLI 架构搭出来的项目: 新增一个域
不用碰组合根,新增一种外部系统支持不用改其他域。

内置的 `schedule` 域可以把这些命令注册成**系统级定时任务**(Windows 任务计划
程序 / macOS launchd / Linux crontab),一次 `schedule install` 之后每天自动跑,
不用再手动执行。调度怎么配置见 [定时调度](#定时调度让它每天自己跑)。

---

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
uv tool install --editable . --force
```

装完之后 `auto-signin-kit` 就是一个全局命令。`--editable` 意味着改代码不用重装,
但如果改了 `pyproject.toml`(尤其是新增域),要重新跑一次
`uv sync && uv tool install --editable . --force`——域注册表写在包的
dist-info 里,只改文件不会自动生效。

---

## 用法

```bash
auto-signin-kit --help                     # 列出所有域
auto-signin-kit workbuddy --help           # 看某个域有哪些命令
auto-signin-kit workbuddy auto             # WorkBuddy 签到 + 成长中心(幂等)
auto-signin-kit workbuddy active           # 查今天是否已完成一次对话(活跃天数只认对话)
auto-signin-kit workbuddy doctor           # 离线检查凭据与解密运行时
auto-signin-kit qoder auto                 # Qoder CN 每日 Credits 领取
auto-signin-kit qoder auto --dry-run       # 只报告可领取项,不真领取
auto-signin-kit trae auto                  # Trae CN 每日签到领取(幂等)
auto-signin-kit trae doctor                # 离线检查登录态可读性与有效期
```

每个命令输出**一行 JSON**(stdout 由机器读,过程日志走 stderr),字段
``result`` / ``report`` / ``needs_attention``;退出码非 0 就是有事要人处理。

静默(`--silent`)模式的结果写进**平台日志目录**(不落在仓库/cwd 根目录):
Windows 为 `%LOCALAPPDATA%\auto-signin-kit\logs\signin.log`(qoder 为
`qoder.log`,trae 为 `trae.log`),`WORKBUDDY_SIGNIN_LOG` /
`QODER_SIGNIN_LOG` / `TRAE_SIGNIN_LOG` 可覆盖;编码固定 UTF-8。

退出码约定:

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 业务失败(网络/认证/领取失败等) |
| 2 | 缺少凭据/配置(需要人工先登录) |
| 3 | 命令用法错误 |
| 4 | 适配层失败(平台能力不可用,如定时任务注册失败) |

凭据来源(按优先级):

- workbuddy 域: 桌面端登录后自动写出的凭据文件,`WORKBUDDY_AUTH_FILE`
  可覆盖;新版加密凭据(`sym-v1` 信封)自动调客户端运行时在隐藏子进程里解密。
- qoder 域: 桌面端数据目录(`%APPDATA%\com.qodercn.app*`,`DPAPI+GCM` 双层
  解密),`QODER_TOKEN` 可覆盖。
- trae 域: 桌面端 storage.json(`%APPDATA%\Trae CN\User\globalStorage`,
  `SS_AES` 信封解密,无需 DPAPI 全平台可用),`TRAE_TOKEN` 可覆盖。
- 敏感值(令牌)在一切输出前统一替换为 `[REDACTED]`。

---

## 定时调度(让它每天自己跑)

`schedule` 域把各域的每日命令注册成**系统级定时任务**。注册完就不用再手动跑了,
机器开着、用户登录着,到点它自己执行。

```bash
auto-signin-kit schedule install              # 注册全部任务(幂等,已存在则更新)
auto-signin-kit schedule install --domain qoder   # 只注册某一个域
auto-signin-kit schedule install --dry-run    # 只打印将要做什么,不落盘
auto-signin-kit schedule status               # 看哪些已注册、下次什么时候跑
auto-signin-kit schedule render               # 打印将要写入系统的 XML / plist / cron 内容
auto-signin-kit schedule uninstall            # 注销(同样支持 --domain / --dry-run)
```

### 默认会注册哪些任务

| 任务名 | 命令 | 触发时间 | 时限 |
|--------|------|----------|------|
| `WorkBuddyAutoSignin` | `workbuddy auto --silent` | 00:05 | 10 min |
| `WorkBuddyGrowthPoll` | `workbuddy poll` | 01/05/09/13/17/21:05 | 5 min |
| `WorkBuddyActiveCheck` | `workbuddy active --silent` | 12/20:05 | 5 min |
| `QoderDailyCredits` | `qoder auto --silent` | 10/11/15/19/23:05 | 5 min |
| `TraeDailyCheckin` | `trae auto --silent` | 09/13/21:05 | 5 min |

`WorkBuddyActiveCheck` 是独立于签到的一类提醒: 活跃天数**只认对话**,领了积分
没对话当天仍算断,它会明确报出来让你当天补得上。

### 改时间不用动代码

编辑 `config/domains/schedule.yaml`:

```yaml
tasks:
  QoderDailyCredits:
    times: ["10:05", "11:05"]   # 一天可多个
    limit_minutes: 5            # 执行时限(分钟)
```

没列进配置的任务沿用 `domains/schedule/data.py` 里的默认值,所以只想改一个任务
时不必把整张表抄一遍。命令行本身不开放配置——它是代码结构,不是可调参数。

执行时限有一条下限: 必须大于对应命令自己的时间预算,否则任务会在脚本收尾前
被平台强杀、丢掉"已领到的部分"的日志。这条由测试
`test_execution_limit_covers_domain_budget` 守着,改小了会在测试期就红。

### 为什么触发时间看起来多得没必要

**故意的**。触发时刻机器可能关机、断网、或刚开机网络还没就绪;一天只有一次
机会的话,撞上就等于当天断签。所有领取接口都是幂等的,多跑不会多领,所以多给
几次机会纯粹是兜底。**不要"优化"掉这些看起来多余的时间点。**

### 平台差异

| 平台 | 机制 | 多个触发时间 | 执行时限 | 输出去向 |
|------|------|--------------|----------|----------|
| Windows | 任务计划程序(schtasks) | 多个 `CalendarTrigger` | `ExecutionTimeLimit` | 任务历史 |
| macOS | `~/Library/LaunchAgents/*.plist` + launchctl | `StartCalendarInterval` 数组 | 不支持(忽略) | plist 里的 `StandardOutPath` |
| Linux | 用户 crontab 的带标记块 | 每个时间一行 | 不支持(忽略) | 各域 `--silent` 写日志文件 |

时限在 launchd/cron 上没有对应项,选择忽略而不是伪造: 各域自带时间预算,超时由
进程自己收尾,比被平台强杀更可控。cron/plist 的改动都带
`# BEGIN/END auto-signin-kit:<任务名>` 标记块,注销时只删自己的块,不动你已有的
其它定时任务。

### 两个前提

- **`auto-signin-kit` 必须在 PATH 里**(`uv tool install` 之后就是)。注册时会
  记下解析到的绝对路径,之后移动/重装记得重新 `install` 覆盖。
- **Windows 上任务只在用户已登录的会话里运行**(以 `InteractiveToken` 注册)。
  这是刻意的: 用户没登录时本来也读不到桌面端的登录态,跑了也没用。

---

## 新增一个域

新增域**不需要**改 `src/auto_signin_kit/cli.py`——它是组合根,只做 entry_points 发现。

1. 复制 `docs/standards/templates/domain/` 到
   `src/auto_signin_kit/domains/<新域名>/`,把里面的占位符和示例域名全局替换成新域名
2. 在 `pyproject.toml` 的 `[project.entry-points."auto_signin_kit.domains"]` 加一行
3. 建配置: `config/domains/<新域名>.yaml` + `config/examples/<新域名>.yaml.example`
4. 补测试: `tests/domains/<新域名>/test_<新域名>.py`
5. `uv sync && uv tool install --editable . --force`
6. 跑门禁 + **手动执行一次真实命令**,不要只看测试绿
7. 如果这个域要每天自动跑: 在 `domains/schedule/data.py` 的 `TASKS` 加一条,
   并同步 `config/domains/schedule.yaml`(时限必须大于该域的时间预算)

---

## 架构约定(硬性规则)

1. **域内部固定三件套:** `schema.py`(域配置)+ `service.py`(纯业务逻辑,
   **禁止 `import typer`**)+ `cli.py`(只做参数解析、调 service、异常映射退出码)。
2. **域与域之间保持独立**;例外显式写进 `pyproject.toml` 的
   `domains 之间彼此独立` 契约的 `ignore_imports`。
3. **配置或用户输入非法就显式报错**,不做静默兜底。
4. **OS 差异只进 `core/adapters/`**(凭据解锁/DPAPI/子进程/注册表/进程枚举都在这),
   业务域禁止平台分支与直接子进程调用(架构门禁强制)。
5. **写操作默认不重试**;只有已确认幂等的接口(签到/查询/活动状态)才开重试。
6. **单文件超过 410 行就拆**(`test_source_file_line_limit` 会拦)。
7. **有多于一种真实实现的 OS 差异才建 `core/ports/`**(目前只有定时调度:
   schtasks / launchd / cron);端口只放 Protocol 与数据对象,由 import-linter
   的 `ports 不依赖 adapters` 契约强制它不反过来依赖具体实现。

本仓库的架构与实现文档(as-built,随代码同步维护):

- `docs/superpowers/specs/` — 架构总览(SPEC-01)与各组件实现文档(SPEC-02~06),索引见该目录 README
- `docs/adr/` — 关键架构决策记录(域注册/端口判据/时间预算)
- `docs/standards/` — 开发规范(STD-01~06,回答"应该是什么样")

完整设计说明见 `polydomain-cli` skill 内的
`references/architecture-decisions.md`;已知反模式见同一 skill 的
`references/pitfalls.md`(均为仓库外文件,不在本仓库 `docs/` 下)。

---

## 目录结构

```
src/auto_signin_kit/
├── cli.py                       # 组合根: 只做 entry_points 发现
├── orchestration/               # pipeline.py + step.py(多步命令: 顺序执行 + 逆序回滚)
├── core/
│   ├── ports/                   # 端口层: 只放 Protocol 与数据对象(定时调度)
│   ├── httpclient.py            # 带时间预算+退避重试的请求层(分桶退避)
│   ├── report.py                # 一行 JSON 结果契约 + 脱敏 + 静默日志双通道
│   ├── config/                  # 惰性配置解析 + pydantic 域 schema 基类
│   ├── errors.py                # 异常→退出码体系(1 基类 / 2 配置 / 3 命令 / 4 适配层)
│   ├── logging.py               # 统一走 stderr,保证 stdout 只含业务输出
│   ├── envvar.py                # 环境变量读取: 统一 AUTO_SIGNIN_KIT_* 前缀
│   ├── prompt.py                # 交互确认注入(contextvars,service 层不 import typer)
│   └── adapters/                # OS 差异层
│       ├── native_helper.py     #   JSON-stdin 凭据助手 / 短命令捕获
│       ├── win_dpapi.py         #   DPAPI + CNG AES-GCM
│       ├── client_discovery.py  #   桌面客户端安装定位(注册表/默认目录/mac bundle)
│       ├── win_registry.py      #   卸载注册表项只读扫描
│       ├── win_process.py       #   运行进程 exe 发现
│       ├── log_dir.py           #   每用户可写日志/缓存目录定位
│       ├── platform_info.py     #   OS 探测与主机信息(平台分支只在本模块)
│       └── scheduler/           #   定时调度: windows(schtasks)/darwin(launchd)/linux(cron)
└── domains/
    ├── workbuddy/               # 签到 + 成长中心(旅行/任务/补登/兑换/盲盒)
    ├── qoder/                   # 每日 Credits(活动查询/领取/设备风控头)
    ├── trae/                    # 每日签到(status/claim,SS_AES 凭据解锁)
    └── schedule/                # 定时调度: 注册/注销/查看每日自动任务
```

文档地图(逆向取证与实现文档分工):

| 文档 | 性质 |
|------|------|
| `docs/notes/NOTE-01~03` | 逆向取证:端点、信封协议与证据链(索引见 `docs/notes/README.md`) |
| `docs/superpowers/specs/SPEC-01~06` | as-built 实现:代码实际是什么样 |
| `docs/adr/ADR-0001~0003` | 架构决策:为什么这样设计 |

---

## 开发

```bash
uv run ruff check && uv run lint-imports && uv run pytest -q
```

**三个都要绿**。新增域时记得同步更新 `AGENTS.md`。

---

## 许可

尚未声明——`pyproject.toml` 里没有 `license` 字段,仓库里也没有 `LICENSE`。

本项目的逆向取证对象是上游 `88lin/workbuddy-auto-signin`(MIT),但本仓库是
重新实现而非它的 fork,许可不能自动继承。公开发布前需要你选定一个,并同步写进
`pyproject.toml` 的 `license` 与 `LICENSE` 文件。
