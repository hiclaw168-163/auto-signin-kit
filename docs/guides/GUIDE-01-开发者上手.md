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

| 域 / 应用 | macOS | Windows | 说明 |
|---|-------|---------|------|
| workbuddy_cn | ✅ | ✅ | 桌面端登录过即可 |
| doubao_cn | ✅ | ✅ | Cookie/CDP 取登录态 |
| trae_cn(声明式应用,经 client_signin 引擎) | ✅ | ✅ | SS_AES 信封解密,全平台可用 |
| qoder | ⚠️ 仅手动注入 `QODER_TOKEN` | ✅ 自动读取 | 非 Windows 自动读会报退出码 2 |
| schedule | launchd | schtasks | Linux 走 crontab |

签到形态分两类(按**凭据来源**分类,见 `STD-07`): 声明式应用走
`web_signin` / `client_signin` 双引擎(YAML 接入,`config/apps/`),编排型
应用是代码域(`doubao_cn` / `workbuddy_cn` / `qoder`,扩展挂在
`domains/client_signin/apps/`)。

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
#    必须带 '.[web]': 不带会把 fastapi/uvicorn 从 tool 环境挤掉,ui serve 起不来
uv sync
uv tool install --editable '.[web]' --force

# ④ 验证: 应列出 client_signin / doubao_cn / qoder / schedule / web_signin / workbuddy_cn
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
#    必须带 '.[web]': 不带会把 fastapi/uvicorn 从 tool 环境挤掉,ui serve 起不来
uv sync
uv tool install --editable '.[web]' --force

# ④ 验证: 应列出 client_signin / doubao_cn / qoder / schedule / web_signin / workbuddy_cn
auto-signin-kit --help
```

### 装完之后

`--editable` 让改源码**立即生效**,但有例外: 改了 `pyproject.toml`——尤其是
**新增域的 entry_points**——必须重跑上面第 ③ 步两条。域注册表写在包的 dist-info
里,只改文件不会自动生效。

不想装全局,用 `uv run auto-signin-kit <域> <命令>` 也一样。

**新机器跑起来(推荐顺序)**:

```bash
auto-signin-kit init                     # 一键初始化: 自检 → 就绪检查 → 注册调度 → 拉起管理台
# 或手动:
auto-signin-kit schedule install         # 注册定时任务(未就绪的应用会如实报出来)
```

桌面客户端登录过、`schedule install` 后用 `auto-signin-kit schedule status`
核对 6/6 就绪。已有一台机器在跑、想把**台账数据**带到新机器: 把旧机器
`data/auto-signin-kit.db` 拷到新机器同路径即可(配置 `config/*.local.yaml`
不入库,需手动带);登录态不用带——各应用在新机器的客户端里重新登录一次,
工具读的是本机客户端的凭据。

要让它每天自己跑: `auto-signin-kit schedule install`(详见根 `README.md`
的「定时调度」)。

### Web 管理台(可选,见 SPEC-09)

不想碰命令行的话,可以装上可选的 web 依赖,然后用浏览器管理。

**一键启动**(推荐): 装好 web 依赖后,双击 `scripts/start-ui.bat`(Windows)或
运行 `scripts/start-ui.sh`(macOS/Linux)——前端没构建会先自动构建(需要
Node.js 18+),然后**后台**起服务并自动打开浏览器,脚本立即退出。
关闭用 `scripts/stop-ui.bat` / `stop-ui.sh`(或 `auto-signin-kit ui stop`)。

手动方式:

```bash
# ① web 依赖(装全局命令时带上,前面第 ③ 步的 '.[web]' 已经包含)
uv tool install --editable '.[web]' --force

# ② 构建前端(或交给一键启动自动补建;产物在 frontend/dist)
auto-signin-kit ui build

# ③ 启动(默认 http://127.0.0.1:8688,只绑本机;--no-open 不弹浏览器)
auto-signin-kit ui serve            # 前台,Ctrl+C 关闭
auto-signin-kit ui serve --daemon   # 后台,配 ui stop / ui status 使用
auto-signin-kit ui status           # 查运行状态与日志路径
auto-signin-kit ui stop             # 停止后台实例(幂等)
```

浏览器打开后: **看板**(各应用今日状态 + 一键手动签到)、**应用**(主档管理/
新建/同步本机应用/编辑声明)、**历史**(签到台账)、**调度**(注册/注销定时
任务)、**诊断**(各应用 doctor 的结构化展示)。
前端没构建也不影响 API——`/api/docs` 有交互文档。CLI 与定时任务**完全不依赖
web 栈**,不装 `[web]` extra 一切照旧。

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
# 代码应用(编排型域)
auto-signin-kit workbuddy_cn auto      # 签到 + 成长中心(幂等,重复跑不会多领)
auto-signin-kit workbuddy_cn active    # 活跃保底: 没有对话记录就自动补一次对话
auto-signin-kit workbuddy_cn doctor    # 离线检查凭据格式与解密运行时
auto-signin-kit qoder auto             # 每日 Credits 领取
auto-signin-kit doubao_cn auto         # 豆包客户端签到

# 声明式应用(YAML 接入,按 kind 选引擎;--app 是应用编码)
auto-signin-kit client_signin run --app trae_cn       # trae 每日签到
auto-signin-kit client_signin doctor --app trae_cn    # 登录态检查
auto-signin-kit client_signin list                    # 列出本机客户端类声明式应用

auto-signin-kit schedule status       # 看定时任务注册情况
auto-signin-kit ui serve              # Web 管理台: 总览/台账等(台账数据读 SQLite,也可直接查库)
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
| `src/auto_signin_kit/core/declarative/` | 声明式共享底座: schema/加载/authkit/executor/常量,双引擎共用 |
| `src/auto_signin_kit/domains/<域>/` | 一个域三件套;`web_signin` / `client_signin` 是声明式引擎 |
| `src/auto_signin_kit/domains/client_signin/apps/` | 代码应用扩展(工厂+策略): doubao_cn / workbuddy_cn / qoder,入口名仍按应用注册 |
| `src/auto_signin_kit/orchestration/` | 多步命令的 Pipeline(顺序执行 + 逆序回滚) |
| `config/domains/` | 各域 YAML 配置,`*.local.yaml` 是本机覆盖、不入库 |
| `config/apps/` | 声明式应用: 一份 `<app_code>.yaml` 一个应用(字段字典见 `STD-07`) |
| `tests/` | 含**架构门禁**(`test_architecture_gate.py`)——不是普通单测 |
| `docs/standards/` | 开发规范 STD-01~07 |
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

声明式应用没有域配置,它的"配置"就是 `config/apps/<app_code>.yaml`
(字段字典与扩展契约见 `STD-07`;`extra="forbid"`,写错键当场报)。注意
`ask_apps` 主档里的 `credits_refresh_time`(积分刷新时刻)不是声明字段,
在 Web 管理台「应用」页改或走 `PUT /api/apps/<code>/state`——它决定
"今日已签"按哪个时刻切天(Qoder 是 10:00,多数应用 00:00)。

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
