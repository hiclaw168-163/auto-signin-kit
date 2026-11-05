# 台账数据库规范化同步指南(2026-11-05)

> 给另一台电脑同步今天这一轮"数据库规范化"改动用。代码全部已推送,
> 按下面步骤走一遍即可;**数据库结构不需要手工改**,首次连接自动迁移。

## 一、今天改了什么(按提交)

| 提交 | 内容 |
|---|---|
| `b12c7e1` | DDL 抽到 `data/sql/` 按表分文件;新增 `core/dto.py`(行 DTO + RunOutcome);三张表补统一审计字段 `created_at/updated_at`;库文件迁到 `data/database/` |
| `d14f291` | `sql/` 从根目录迁入 `data/sql/`(`.gitignore` 开例外 `!data/sql/`,其余 data 内容仍不入库) |
| `724aa6c` | 列名避开 SQLite 关键字:`action` → `operation`;三表列序按习惯重排(主体列在前、审计字段殿后),老库自动重建对齐 |
| `24d5710` | 列名按 MySQL 官方关键字表清零:`account` → `account_name`、视图列 `day` → `checkin_day`;修 UI 前端"发版仍旧版"(index.html 统一 no-cache) |

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

## 三、另一台电脑的同步步骤

```bash
# 1. 拉代码(注意 uv.lock 本地镜像噪音不用管,以远端为准)
git pull

# 2. 装依赖(项目 venv 不含 web extra,属正常)
uv sync

# 3. 重装 tool(必须带 [web],否则 ui serve 起不来)
uv tool install --editable '.[web]' --force

# 4. 首次运行任意命令时,老库自动迁移(幂等):
#    补 created_at/updated_at → action→operation → account→account_name →
#    列序重建 → 视图重建。跑一条验证:
auto-signin-kit doubao status

# 5. (可选)要跑 Web 管理台才需要:
cd frontend && npm install && npm run build
auto-signin-kit ui serve --daemon     # 注意:必须用 tool 可执行文件,不要 uv run
```

### 数据库迁移验证(可选)

```bash
sqlite3 data/database/auto-signin-kit.db "PRAGMA table_info(ask_signin_runs);"
# 期望列序: id domain account_name executed_at operation source result
#           needs_attention report request response detail created_at updated_at
sqlite3 data/database/auto-signin-kit.db "SELECT COUNT(*) FROM ask_signin_runs;"
```

## 四、要把台账历史数据带过去吗?

- **只同步结构**:什么都不用拷,新机器首次连接自动建好空库。
- **连历史数据一起带**(推荐做法):先在本机 `auto-signin-kit ui stop`,
  把 `data/database/auto-signin-kit.db` 拷到新机同路径(覆盖/放置),
  新机跑任意命令时迁移会对这份老库自动生效。

## 五、不随 git 走的机器本地项(需要各自配)

| 项 | 位置 | 说明 |
|---|---|---|
| launchd 调度任务 | 系统级 | 新机要 `auto-signin-kit schedule install` 重新注册 |
| 豆包(签到模式)启动器 | 桌面 `豆包(签到模式).command` | 从 `scripts/doubao-signin-mode.command` 复制,豆包 cookie 是各机客户端各自的 |
| 台账/日志数据 | `data/`、`logs/` | gitignore,按需拷贝 |

## 六、迁移失败怎么办?

迁移全部幂等且单事务,中断重连即可重来。若报 `data/sql/ 目录定位失败`,
说明不是 editable 安装——按第三节第 3 步重装 tool。
