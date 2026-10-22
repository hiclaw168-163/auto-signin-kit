# GUIDE-01 · 开发者上手

> 从拿到仓库到能改能跑。规范细则见 `docs/standards/`,代码现状见
> `docs/superpowers/specs/`,报错排查见 [GUIDE-03](GUIDE-03-排错手册.md)。

## 1. 你需要什么

- [uv](https://docs.astral.sh/uv/)(依赖、虚拟环境、全局安装都走它)——**第 2 节的
  分平台安装里已经包含了装它的命令**,没有 uv 直接跟着跑就行;Python 3.11+
  由 uv 自动准备,不用手动装
- git(拉仓库用)
- 要真跑签到命令: 对应的**桌面客户端在本机登录过一次**。工具不替你登录,
  它只是从客户端数据目录里读已有凭据(见各域 SPEC 的凭据链一节)。
- 只读检查不需要登录: `doctor`(离线查凭据格式与解密能力)、
  `qoder auto --dry-run`(只报告可领取项)、`schedule render`(只打印不落盘)。

各域对操作系统的支持不一样(qoder 的自动凭据读取依赖 Windows DPAPI):

| 域 | macOS | Windows | 说明 |
|---|-------|---------|------|
| workbuddy | ✅ | ✅ | 桌面端登录过即可 |
| trae | ✅ | ✅ | SS_AES 信封解密,全平台可用 |
| qoder | ⚠️ 仅手动注入 `QODER_TOKEN` | ✅ 自动读取 | 非 Windows 自动读会报退出码 2 |
| schedule | launchd | schtasks | Linux 走 crontab |

## 2. 安装(分平台,整段复制执行)

每个平台从上到下**整段复制、逐条执行**即可,不需要预先装好 Python 或 uv。
装完 uv 后如果提示找不到命令,**重开一个终端**再继续后面的步骤。

### macOS / Linux(zsh/bash)

```bash
# ① 装 uv(已经装过可跳过;验证: uv --version)
curl -LsSf https://astral.sh/uv/install.sh | sh

# ② 拉仓库(本仓库在 GitHub 与 Gitee 同步维护,任选其一,地址以你获取到的地方为准)
git clone <仓库地址>
cd auto-signin-kit

# ③ 安装依赖并装成全局命令(Python 3.11+ 由 uv 自动准备)
uv sync
uv tool install --editable . --force

# ④ 验证: 应列出 workbuddy / qoder / trae / schedule
auto-signin-kit --help
```

### Windows(PowerShell)

```powershell
# ① 装 uv(已经装过可跳过;验证: uv --version)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# ② 拉仓库(本仓库在 GitHub 与 Gitee 同步维护,任选其一,地址以你获取到的地方为准)
git clone <仓库地址>
cd auto-signin-kit

# ③ 安装依赖并装成全局命令(Python 3.11+ 由 uv 自动准备)
uv sync
uv tool install --editable . --force

# ④ 验证: 应列出 workbuddy / qoder / trae / schedule
auto-signin-kit --help
```

### 装完之后

`--editable` 让改源码**立即生效**,但有例外: 改了 `pyproject.toml`——尤其是
**新增域的 entry_points**——必须重跑上面第 ③ 步两条。域注册表写在包的 dist-info
里,只改文件不会自动生效。

不想装全局,用 `uv run auto-signin-kit <域> <命令>` 也一样。

要让它每天自己跑: `auto-signin-kit schedule install`(详见根 `README.md`
的「定时调度」)。

### 卸载

**顺序有讲究: 必须先撤定时任务、再卸命令**——`schedule uninstall` 依赖
`auto-signin-kit` 本身,反过来定时任务会照常触发一个已经不存在的命令。

```bash
# ① 撤掉所有定时任务(可选 --domain workbuddy 只撤一个域)
auto-signin-kit schedule uninstall

# ② 移除全局命令
uv tool uninstall auto-signin-kit
```

可选清理: 运行日志默认在项目 `logs/` 目录(或 `AUTO_SIGNIN_KIT_LOG_DIR`
指定的目录),运行数据(SQLite 签到台账)在项目 `data/` 目录(或
`AUTO_SIGNIN_KIT_DATA_DIR`),各自随仓库目录一起删除;非项目环境下在平台目录
(macOS `~/Library/Logs/auto-signin-kit`、Windows
`%LOCALAPPDATA%\auto-signin-kit`)。

不再使用的话,删掉仓库目录即可——代码、`.venv`、配置都在里面。

## 3. 命令面

```bash
auto-signin-kit workbuddy auto      # 签到 + 成长中心(幂等,重复跑不会多领)
auto-signin-kit workbuddy active    # 活跃保底: 没有对话记录就自动补一次对话
auto-signin-kit workbuddy doctor    # 离线检查凭据格式与解密运行时
auto-signin-kit qoder auto          # 每日 Credits 领取
auto-signin-kit trae auto           # 每日签到领取
auto-signin-kit history             # 签到台账: 每天/每域结果一览(不必翻日志)
auto-signin-kit schedule status     # 看定时任务注册情况
```

每个命令输出**一行 JSON 事件**(stdout 给机器读,过程日志走 stderr),关键字段
`ts`(ISO 8601 带时区)/ `level`(`ERROR`=needs_attention)/ `domain` /
`result` / `report` / `needs_attention`。日志文件是同样的 JSON Lines 格式
(定位问题: `jq -c 'select(.level=="ERROR")' logs/*.log`)。退出码:

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 业务失败(网络/认证/领取失败) |
| 2 | 缺少凭据或配置(**需要你先登录客户端**) |
| 3 | 命令用法错误 |
| 4 | 适配层失败(平台能力不可用,如定时任务注册失败) |

`--silent` 的结果写进平台日志目录而不是 stdout,配合定时任务使用。

## 4. 仓库地图

| 目录 | 放什么 |
|------|--------|
| `src/auto_signin_kit/cli.py` | 组合根: **只做 entry_points 发现**,加域不改它 |
| `src/auto_signin_kit/core/` | 跨域公共层: 请求、配置、报告、日志、端口与平台适配器 |
| `src/auto_signin_kit/domains/<域>/` | 一个助手一个域,固定三件套 |
| `src/auto_signin_kit/orchestration/` | 多步命令的 Pipeline(顺序执行 + 逆序回滚) |
| `config/domains/` | 各域 YAML 配置,`*.local.yaml` 是本机覆盖、不入库 |
| `tests/` | 含**架构门禁**(`test_architecture_gate.py`)——不是普通单测 |
| `docs/standards/` | 开发规范 STD-01~06 |
| `docs/superpowers/specs/` | as-built 文档 SPEC-01~07 |
| `docs/notes/` | 逆向取证 NOTE-01~03(端点与协议是怎么知道的) |
| `docs/adr/` | 架构决策记录 |
| `docs/guides/` | 本目录: 操作指南 |

域内三件套是硬约束: `schema.py`(配置模型)+ `service.py`(业务,**禁止 import
typer**)+ `cli.py`(只做参数解析、调 service、映射退出码)。

## 5. 日常开发循环

```bash
uv run ruff check && uv run lint-imports && uv run pytest -q
```

**三条都要绿**,少跑一条都不算完:

| 命令 | 拦什么 |
|------|--------|
| `ruff check` | 风格与明显缺陷(行宽、未用 import 等) |
| `lint-imports` | 架构契约: 域之间彼此独立、`core` 不被反向依赖、`ports` 不依赖 `adapters` |
| `pytest` | 行为单测 + 架构门禁(单文件行数、service 无 typer、cli 无业务 import 等) |

改完还要**手动真实跑一次命令**。测试绿 ≠ 行为对: 定时任务那块就是测试全绿、
真机注册才暴露出 `LogonType` 的问题(见 [GUIDE-03](GUIDE-03-排错手册.md))。

## 6. 配置怎么改

三层,后者覆盖前者:

1. `config/domains/<域>.yaml` — 入库的默认值
2. `config/domains/<域>.local.yaml` — 本机覆盖,**已被 .gitignore 排除**,放本机
   路径等不该公开的差异
3. 环境变量 — `AUTO_SIGNIN_KIT_*` 是跨域前缀;各域另有自己的前缀
   (`WORKBUDDY_*` / `QODER_*` / `TRAE_*`,见对应 SPEC)

配置模型是 `extra="forbid"`: **写了 schema 里没有的键会直接报错**,不会静默忽略。
想改调度触发时间,改 `config/domains/schedule.yaml` 的 `tasks`,不用动代码。

## 7. 改了代码,还要改什么

本仓库把"文档失同步"当 bug 处理,改完代码顺手对一遍:

| 你改了 | 要同步 |
|--------|--------|
| 域的结构/端点/配置键/退出码 | 对应 SPEC(`docs/superpowers/specs/`) |
| 用户可见行为(命令、配置、退出码) | 根 `README.md` + 本目录指南 |
| 新逆向到的端点或协议 | `docs/notes/` 加 NOTE + 更新该目录 README 索引 |
| 架构级取舍 | `docs/adr/` 加 ADR |
| 新增/重命名文档 | 对应目录的 `README.md` 索引表 |

## 8. 下一步

- 要让签到每天自己跑 → 根 `README.md` 的「定时调度」
- 要支持一个新的助手 → [GUIDE-02](GUIDE-02-新增一个域.md)
- 命令跑不通 → [GUIDE-03](GUIDE-03-排错手册.md)
