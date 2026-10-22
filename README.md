# auto-signin-kit

**AI 编程助手每日签到自动化工具箱** | *Daily auto check-in & rewards toolkit for AI coding assistants*

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

每天手动签到,忘了就断积分?让这台机器替你记得——配置一次,以后每天自动签。

| 支持的 AI 编程助手 | 能做什么 |
|---|---|
| **WorkBuddy / CodeBuddy 系** | 每日签到领积分;成长中心活跃保底(没对话就自动补一条,活跃天数不断) |
| **Qoder CN** | 活动窗口内自动领取 Credits(幂等,重复跑不多领) |
| **Trae CN** | 每日签到领 Credits(1024 活动限流期自动多轮重试) |

- **一次配置,每天自动跑** — 内置 `schedule` 域把签到命令注册成系统级定时任务
  (Windows 任务计划程序 / macOS launchd / Linux crontab),`schedule install` 之后无需再管
- **结果看得见** — `history` 域提供 SQLite 签到台账: 每天/每域结果一目了然,
  服务端历史(活跃日历、领取记录)一键回填,不必翻日志
- **加一个签到目标很省事** — 多域 CLI 插件架构: 新增一个域不用碰组合根,
  新增一种外部系统支持不用改其他域

用 `polydomain-cli` 的多域 CLI 架构搭出来的项目。第一次接触这个仓库想动手改?
操作指南在 `docs/guides/`
([上手](docs/guides/GUIDE-01-开发者上手.md) / [新增域](docs/guides/GUIDE-02-新增一个域.md) /
[排错](docs/guides/GUIDE-03-排错手册.md))。

> 关键词: Trae 签到 / Qoder 自动签到 / WorkBuddy CodeBuddy 签到 / AI 编程助手积分 /
> check-in automation / daily rewards bot / auto signin script / scheduled task

---

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

> 从零开始(连 uv 都没有)的 **macOS / Windows 分平台一键安装与卸载**步骤,
> 见 [GUIDE-01 开发者上手](docs/guides/GUIDE-01-开发者上手.md)。

```bash
uv sync
uv tool install --editable . --force
```

装完之后 `auto-signin-kit` 就是一个全局命令。`--editable` 意味着改代码不用重装,
但如果改了 `pyproject.toml`(尤其是新增域),要重新跑一次
`uv sync && uv tool install --editable . --force`——域注册表写在包的
dist-info 里,只改文件不会自动生效。

卸载(顺序别反——`schedule uninstall` 依赖命令本身;先卸命令的话,定时任务会
照常触发一个已经不存在的命令):

```bash
auto-signin-kit schedule uninstall    # 撤掉全部定时任务(可选 --domain 只撤一个域)
uv tool uninstall auto-signin-kit     # 移除全局命令
```

可选清理: 运行日志默认在项目 `logs/` 目录(或 `AUTO_SIGNIN_KIT_LOG_DIR`
指定的目录),运行数据(SQLite 台账)在项目 `data/` 目录(或
`AUTO_SIGNIN_KIT_DATA_DIR`),各自随目录一起删除即可;非项目环境下在平台目录
(macOS `~/Library/Logs/auto-signin-kit`、Windows
`%LOCALAPPDATA%\auto-signin-kit`)。

---

## 用法

```bash
auto-signin-kit --help                     # 列出所有域
auto-signin-kit workbuddy --help           # 看某个域有哪些命令
auto-signin-kit workbuddy auto             # WorkBuddy 签到 + 成长中心(幂等)
auto-signin-kit workbuddy active           # 活跃保底: 没有对话记录就自动补一次对话
auto-signin-kit workbuddy doctor           # 离线检查凭据与解密运行时
auto-signin-kit qoder auto                 # Qoder CN 每日 Credits 领取
auto-signin-kit qoder auto --dry-run       # 只报告可领取项,不真领取
auto-signin-kit trae auto                  # Trae CN 每日签到领取(幂等)
auto-signin-kit trae doctor                # 离线检查登录态可读性与有效期
auto-signin-kit history                    # 签到台账: 每天/每域结果一览,不必翻日志
auto-signin-kit history import-server      # 把服务端的历史记录(活跃日历等)回填进台账
```

每个命令输出**一行 JSON 事件**(stdout 由机器读,过程日志走 stderr),字段
``ts``(ISO 8601 带时区)/ ``level``(`ERROR`=needs_attention)/ ``domain`` /
``result`` / ``report`` / ``needs_attention``;日志文件是同样的 JSON Lines
格式,`jq -c 'select(.level=="ERROR")' logs/*.log` 可直接过滤;退出码非 0
就是有事要人处理。

静默(`--silent`)模式的结果写进结果日志(不落在仓库/cwd 根目录),目录优先级:

1. 域级环境变量指定的完整路径(`WORKBUDDY_SIGNIN_LOG` / `QODER_SIGNIN_LOG` /
   `TRAE_SIGNIN_LOG`);
2. `AUTO_SIGNIN_KIT_LOG_DIR` 环境变量指定的目录(跨平台行为一致);
3. 默认**项目的 `logs/` 目录**(从仓库运行或可编辑安装时),文件名统一为
   `<域名>.log`——即 `logs/workbuddy.log` / `logs/qoder.log` / `logs/trae.log`;
4. 定位不到项目根时回退平台目录: Windows
   `%LOCALAPPDATA%\auto-signin-kit\logs`、macOS `~/Library/Logs/auto-signin-kit`、
   Linux `$XDG_STATE_HOME/auto-signin-kit/log`(缺省 `~/.local/state/...`)。

编码固定 UTF-8。`logs/` 已被 `.gitignore` 排除,不会误提交。

**运行数据与日志分居**: 签到台账(`data/auto-signin-kit.db`,SQLite)是积累的
事实,不跟可清理的日志放一起——目录优先级: `AUTO_SIGNIN_KIT_DATA_DIR` 环境变量
> 项目的 `data/` 目录(`.gitignore` 已排除)> 平台目录
`%LOCALAPPDATA%\auto-signin-kit\data`(Windows)/ macOS Application Support。
用 `auto-signin-kit history` 看结果,不必翻日志。

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
  解密;**自动读取仅 Windows**,macOS/Linux 需设置 `QODER_TOKEN`),`QODER_TOKEN`
  可覆盖。
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
| `WorkBuddyGrowthPoll` | `workbuddy poll` | 01/09/17/21:05 | 5 min |
| `WorkBuddyActiveCheck` | `workbuddy active --silent` | 12/20:05 | 5 min |
| `QoderDailyCredits` | `qoder auto --silent` | 10/11/15/19/23:05 | 5 min |
| `TraeDailyCheckin` | `trae auto --silent` | 09/13/21:05 | 5 min |

`WorkBuddyActiveCheck` 是独立于签到的活跃保底: 活跃天数**只认对话**,领了积分
没对话当天仍算断。它发现当天没有活跃记录时会**自动补一次对话**并复查记分,
不再只是提醒。

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
│   ├── store.py                 # SQLite 签到台账(结果落库,见 SPEC-07)
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
    ├── schedule/                # 定时调度: 注册/注销/查看每日自动任务
    └── history/                 # 签到台账: 每日状态一览/运行明细/日志回填
```

文档地图(逆向取证与实现文档分工):

| 文档 | 性质 |
|------|------|
| `docs/notes/NOTE-01~03` | 逆向取证:端点、信封协议与证据链(索引见 `docs/notes/README.md`) |
| `docs/superpowers/specs/SPEC-01~07` | as-built 实现:代码实际是什么样 |
| `docs/adr/ADR-0001~0003` | 架构决策:为什么这样设计 |
| `docs/guides/GUIDE-01~03` | 操作指南: 上手 / 新增域 / 排错(索引见 `docs/guides/README.md`) |

---

## 开发

```bash
uv run ruff check && uv run lint-imports && uv run pytest -q
```

**三个都要绿**。新增域时记得同步更新 `AGENTS.md`。

---

## 许可

[MIT](LICENSE)。
