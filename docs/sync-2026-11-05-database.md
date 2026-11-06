# 跨机器同步指南(2026-11-05 数据库规范化 + 2026-11-06 双引擎)

> 给另一台电脑同步这两轮改动用。代码全部已推送,
> 按下面步骤走一遍即可;**数据库结构不需要手工改**,首次连接自动迁移。

## 一、这两轮改了什么(按提交)

| 提交 | 内容 |
|---|---|
| `b12c7e1` | DDL 抽到 `data/sql/` 按表分文件;新增 `core/dto.py`(行 DTO + RunOutcome);三张表补统一审计字段 `created_at/updated_at`;库文件迁到 `data/database/` |
| `d14f291` | `sql/` 从根目录迁入 `data/sql/`(`.gitignore` 开例外 `!data/sql/`,其余 data 内容仍不入库) |
| `724aa6c` | 列名避开 SQLite 关键字:`action` → `operation`;三表列序按习惯重排(主体列在前、审计字段殿后),老库自动重建对齐 |
| `24d5710` | 列名按 MySQL 官方关键字表清零:`account` → `account_name`、视图列 `day` → `checkin_day`;修 UI 前端"发版仍旧版"(index.html 统一 no-cache) |
| `e1839bc` | **client_signin 双引擎**:`core/declarative` 共享底座,声明式应用按 `kind`(凭据来源)路由;doubao_cn/workbuddy_cn/qoder 迁为 `client_signin/apps/` 扩展(命令入口名不变);trae_cn kind 规整为 client;ask_apps 加 `credits_refresh_time`(Qoder=10:00),签到日按积分周期窗口判断;类别/来源文案统一常量;前端档案字段/布局修复 |
| `3d8837e` | GUIDE-01/README 同步现状,补新机器上手流程 |

(更早:doubao 链路 `8325996`/`8ad0c2b`,CDP 端口进配置 + 启动器。)

## 二、数据库结构变化(新库/老库最终一致)

三张表 `ask_signin_runs` / `ask_accounts` / `ask_schedule_changes`:

1. **统一审计字段(所有表必备)**:`created_at`、`updated_at`(本地无时区,
   写层维护;老行已回填)。
2. **列名规约(避开数据库关键字)**:
   - `action` → `operation`(SQLite 关键字 ACTION)
   - `account` → `account_name`(MySQL 关键字 ACCOUNT)
   - 视图输出列 `day` → `checkin_day`(MySQL 保留字 DAY)
3. **列序习惯**:主键 → 业务主体(domain/account_name)→ 时间 → 动作状态 →
   明细 JSON(request/response/detail)→ 审计字段。
4. **库文件位置**:`data/database/auto-signin-kit.db`(原 `data/` 根下已迁入子目录)。
5. DDL 唯一来源是 `data/sql/` 目录(按表分文件 + indexes.sql + views.sql),
   `store_migrations.py` 只装载执行。

主档表 `ask_apps`(2026-11-06 增量):

1. 新列 `credits_refresh_time`(HH:MM,积分刷新时刻):老库自动补列并回填
   ——`qoder` = `10:00`,其余 = `00:00`;调度跳过/"今日已签"按该时刻起的
   积分周期窗口判断,不是自然日。
2. `trae_cn` 的 `kind` 自动规整为 `client`(分类标准 = 凭据来源,trae 凭据
   从桌面端本地存储解锁)。
3. `trae_cn` 的 `app_desc` 由"应用同步"补齐(**只补空**),见第三节第 6 步。

## 三、另一台电脑的同步步骤

```bash
# 1. 拉代码(注意 uv.lock 本地镜像噪音不用管,以远端为准)
git pull

# 2. 装依赖(项目 venv 不含 web extra,属正常)
uv sync

# 3. 重装 tool(必须带 [web];这轮 entry_points 变了——新增 client_signin、
#    三个应用改挂 client_signin.apps——不重装命令面是旧的)
uv tool install --editable '.[web]' --force

# 4. 验证命令面(应能看到 client_signin):
auto-signin-kit client_signin list        # trae_cn 应显示 kind=client、ready ✓

# 5. 首次运行任意命令时,老库自动迁移(幂等):
#    补审计字段 → 列名规整 → 列序重建 → ask_apps 补 credits_refresh_time +
#    kind 回填。验证:
sqlite3 data/database/auto-signin-kit.db \
  "SELECT app_code, kind, credits_refresh_time FROM ask_apps;"
# 期望: qoder 行 credits_refresh_time=10:00,trae_cn 行 kind=client

# 6. 重装调度任务(任务名变了,见第四节;未就绪的应用会如实报出来):
auto-signin-kit schedule install
auto-signin-kit schedule status           # 核对 6/6 就绪

# 7. 补 trae_cn 描述到主档(Web 管理台「应用」页点"同步本机应用",或):
curl -s -X POST http://127.0.0.1:8688/api/apps/sync

# 8. (可选)要跑 Web 管理台才需要(前端这轮改了,必须重新构建):
cd frontend && npm install && npm run build
auto-signin-kit ui serve --daemon     # 注意:必须用 tool 可执行文件,不要 uv run
```

## 四、调度任务改名(2026-11-06)

trae 的任务从 `WebSigninTraeCn` 改为 `ClientSigninTraeCn`,argv 变为
`client_signin run --app trae_cn --silent`。`schedule install` 只装注册表里
的现役任务,**旧名字的 plist 不会自动删**,重装后检查并清理:

```bash
ls ~/Library/LaunchAgents/ | grep auto-signin-kit
# 若还有 com.auto-signin-kit.WebSigninTrae*.plist(旧名),清掉:
launchctl bootout gui/$(id -u)/com.auto-signin-kit.WebSigninTraeCn 2>/dev/null
rm -f ~/Library/LaunchAgents/com.auto-signin-kit.WebSigninTrae*.plist
```

Windows 用 schtasks,同理删旧任务名再 `schedule install`。

## 五、要把台账历史数据带过去吗?

- **只同步结构**:什么都不用拷,新机器首次连接自动建好空库。
- **连历史数据一起带**(推荐做法):先在本机 `auto-signin-kit ui stop`,
  把 `data/database/auto-signin-kit.db` 拷到新机同路径(覆盖/放置),
  新机跑任意命令时迁移会对这份老库自动生效(包括 credits_refresh_time
  补列与 kind 回填)。
- **登录态不用带**:各应用凭据读的是本机客户端的本地存储,在新机器的
  客户端里重新登录一次即可(Windows qoder 自动读 DPAPI;macOS qoder
  需手动注入 `QODER_TOKEN`)。

## 六、不随 git 走的机器本地项(需要各自配)

| 项 | 位置 | 说明 |
|---|---|---|
| launchd/schtasks 调度任务 | 系统级 | 新机要 `auto-signin-kit schedule install` 重新注册;旧任务名 plist 手动清(第四节) |
| 豆包(签到模式)启动器 | 桌面 `豆包(签到模式).command` | 从 `scripts/doubao-signin-mode.command` 复制,豆包 cookie 是各机客户端各自的 |
| 台账/日志数据 | `data/`、`logs/` | gitignore,按需拷贝 |
| 本机配置覆盖 | `config/**/*.local.yaml` | gitignore,按需拷贝 |

## 七、迁移失败怎么办?

迁移全部幂等且单事务,中断重连即可重来。若报 `data/sql/ 目录定位失败`,
说明不是 editable 安装——按第三节第 3 步重装 tool。

> **Windows 机同步实测补充(2026-11-06)**: 库文件从 `data/` 根迁入
> `data/database/` 没有自动搬运逻辑,老库要手动挪(macOS 当时是手动 mv 的);
> 搬前先 `ui stop` 杀干净进程(残留进程会锁文件,`mv` 报 Device or resource
> busy 时用 `cp` 兜底)。老库若已在新 schema 下建过 `ask_apps`,app_code
> `_cn` 规整改名会被跳过/撞 UNIQUE——已在 `store_migrations` 修为
> `PRAGMA user_version` 门闩 + 账户碰撞合并,两边拉到最新代码后重连一次
> 自动收敛。

架构门禁自检(改过代码的话):

```bash
uv run ruff check && uv run lint-imports && uv run pytest -q
```
