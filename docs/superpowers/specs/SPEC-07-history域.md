# SPEC-07-history域

> **概要**: 签到结果的**规整台账**——SQLite 单文件存储 + `history` 查询命令,
> 回答"这周有没有断签/失败",不用再翻 JSONL 日志。
> **状态**: 已实现(本机验证: 历史回填 9 条、调度任务自动落库、测试隔离生效)
> **关联**: [SPEC-01](SPEC-01-架构总览.md) · [SPEC-02](SPEC-02-core层公共契约.md) ·
> [SPEC-03](SPEC-03-workbuddy域.md) / [SPEC-04](SPEC-04-qoder域.md) / [SPEC-05](SPEC-05-trae域.md)

## 1. 数据流与模块分工

```
各域 service → emit_result(core/report.py) → 日志文件/stdout(不变)
                                          └→ store.record_payload(best-effort 落库)
history 域 → 读 SQLite 台账 → 表格(JSONL 可选)
```

| 模块 | 职责 |
|------|------|
| `core/store.py` | 台账层: 建表/写入/分流;**best-effort**,任何 DB 错误静默吞掉,绝不影响签到 |
| `domains/history/service.py` | 台账查询(每日状态/明细)与 JSONL 回填(幂等去重) |
| `domains/history/cli.py` | 表格渲染(rich)与参数解析;`--json` 时输出 JSONL |

写入点是 `emit_result` 的**单一咽喉**:所有域的结果(含调度域)自动进台账,
域代码零改动。落库内容是**脱敏后的 JSON 行**——令牌进库前已替换 `[REDACTED]`。

## 2. 表设计(统一 `ask_` 前缀 + 见名知义,不用笼统大表)

所有对象统一 `ask_` 前缀(auto-signin-kit),同类对象一眼归属:

| 对象 | 类型 | 内容 |
|------|------|------|
| `ask_signin_runs` | 实体表 | **签到运行台账**:一行一次运行(ts/domain/action/result/needs_attention/report/detail);`detail` 存完整事件 JSON,连签天数、积分等统计都在里面(SQLite `json_extract` 可查),**不另建派生统计表**——两份真相必然漂移 |
| `ask_schedule_changes` | 实体表 | **调度变更审计**:定时任务的注册/注销(action/platform/tasks/report) |
| `ask_daily_checkins` | 视图 | **每天每域最后一次运行 = 当天最终状态**;把一天多次运行折叠成一行,`history` 默认视图与人工查库都读它 |

不按域分表(workbuddy_runs / qoder_runs / …)的理由:台账的核心价值是**跨域
一张表看全局**;按域分表会把"本周有没有断签"变成 UNION 拼接,且新增域就得改
公共层,违反多域架构。域信息是 `signin_runs.domain` 一列,不是一张表。

### 2.1 来源标识(source 列)

查数据先分清"这行是真实执行还是回填",两个标识列分工明确:

| 列 | 语义 | 取值 |
|----|------|------|
| `source` | **数据哪来的**(规范标识,建了索引) | `run` 正常执行(emit_result 落库) / `backfill` 回填(import-logs / import-server) |
| `action` | **干了什么** | 正常: 当时的命令(auto/active/claim…);回填: 统一 `backfill` |

细分来源在 `detail.source`:`log-import`(日志导入)/ `server-backfill`
(服务端导入)。查"只看真实执行"一条 SQL:`WHERE source='run'`;命令行等价物
是 `history events --source run`。老库(2026-10-22 前)由 store.connect() 的
迁移逻辑自动补列并回填标识,正常运行的旧行 action 可能为空(当时没传)。

文件位置:项目 `data/auto-signin-kit.db`(**与日志分居**——日志是事件流,可清
可重建;台账是积累的事实,清理日志不能误伤它),随 `AUTO_SIGNIN_KIT_DATA_DIR`
整体迁移,被 `.gitignore` 排除。目录定位收口在
`core/adapters/log_dir.default_data_dir()`。

## 3. 命令

```bash
auto-signin-kit history                      # 最近 7 天每日状态(--days 改回看天数)
auto-signin-kit history --attention          # 只看需要人工处理的
auto-signin-kit history --date 2026-10-22    # 某一天的运行明细
auto-signin-kit history events --source run  # 运行明细,只看真实执行(--source backfill 只看回填)
auto-signin-kit history import-logs          # 把现有 JSONL 日志回填进台账(幂等去重)
auto-signin-kit history import-server        # 服务端还能查到的历史回填进台账
```

### 3.1 服务端历史回填(import-server)

日志只覆盖"装了本工具之后";服务端自己存的历史由 `import-server` 整理:

| 域 | 数据源 | 能回填什么 | 覆盖 |
|----|--------|-----------|------|
| workbuddy | `GET /v2/activity/growth/heatmap` | 逐日活跃 score → ACTIVE/NO_ACTIVITY | **约一年**(2026-10 实测回填 365 行) |
| qoder | `GET /sash/api/v1/me/campaigns` | `claimStatus=CLAIMED` 的活动窗口 → CLAIMED | 服务端保留的历史窗口 |
| trae | — | 只有当日状态,**没有历史接口** | 如实跳过,不伪造 |

回填行带 `action="backfill"` 与 `detail.source="server-backfill"` 标记,`ts`
取当日固定时刻,去重键天然幂等(重复执行不产生重复行)。当天的真实运行记录
ts 晚于回填行,视图 `ask_daily_checkins` 自然优先展示真实运行。
本域是唯一跨域 import 的地方(读各域接口层),已在 pyproject `ignore_imports`
显式登记。

默认输出 rich 表格(人看);`--json` 输出 JSONL(机器)。这是对"一行 JSON"契约的
刻意偏离——台账命令的受众是人不是自动化,已在 SPEC-01 记录该例外。

## 4. 回填与去重

`import-logs` 同时认两种行格式(新版 JSONL 行内带 `ts`;旧版
`[YYYY-MM-DD HH:MM:SS] {json}` 时间戳在行首),解析不了的行静默跳过。
去重键 `(ts, domain, result, report)`——重复导入只跳过不加行。

## 5. 测试隔离(踩过的坑)

台账写入端在 emit_result,而**测试也走 emit**——不隔离的话,跑一次测试套件就把
`NO_AUTH` 假记录写进真实台账(2026-10-22 实际发生,台账里混进了 pytest 临时
目录路径的行)。修复:根 `tests/conftest.py` autouse 把 `AUTO_SIGNIN_KIT_LOG_DIR`
与 `AUTO_SIGNIN_KIT_DATA_DIR` 都指到每测试的临时目录——日志与台账分别从两个
环境变量解析,一处隔离两边生效。**任何新增的"从日志/数据目录解析路径"的存储
都必须过这道隔离**。

## 6. 失效模式

- 台账文件被删 → 重建空表,历史靠 `import-logs` 从日志找回(日志是事实源头,
  台账是规整视图,二者可互相重建)
- 并发写冲突 → WAL + 3s busy_timeout 已覆盖;极端情况写入失败静默放弃,
  日志仍是完整事实源
