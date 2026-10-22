# SPEC-06-schedule域

> **概要**: 让各域的签到/领奖**每天自动执行**——注册、注销、查看系统级定时任务。
> **状态**: 已实现(本机真实验证过: 5 个任务全部注册成功,schtasks 查询状态"就绪"、
> 下次运行时间正确;注销路径为幂等桩测,未在本机实删)
> **关联**: [SPEC-01](SPEC-01-架构总览.md) · [SPEC-02](SPEC-02-core层公共契约.md) ·
> 设计来源 [NOTE-01 §11 定时调度的两条依据](../../notes/NOTE-01-workbuddy域接口取证.md)

## 1. 模块分工(4 文件)

| 文件 | 职责 |
|------|------|
| `cli.py` | 命令入口(install / uninstall / status / render);只做参数解析与退出码映射 |
| `service.py` | `run_action` 分发;install/uninstall 逐个执行且**失败不中断**,status 查询,render_definitions |
| `data.py` | 任务清单 `TASKS`(名称/命令行/触发时间/时限)+ `tasks_for(domain)` 过滤 |
| `schema.py` | `ScheduleDomainConfig`,只有 `enabled_tasks` 一个开关 |

OS 差异全部在 `core/adapters/scheduler/`,本域只见 `core.ports.scheduler.Scheduler`
协议——域内不出现 schtasks / launchctl / crontab,也不出现平台分支。

## 2. 任务清单(data.py)

| 任务名 | 命令 | 触发时间 | 时限 |
|--------|------|----------|------|
| `WorkBuddyAutoSignin` | `workbuddy auto --silent` | 00:05 | PT10M |
| `WorkBuddyGrowthPoll` | `workbuddy poll` | 01/05/09/13/17/21:05 | PT5M |
| `WorkBuddyActiveCheck` | `workbuddy active --silent` | 12/20:05 | PT5M |
| `QoderDailyCredits` | `qoder auto --silent` | 10/11/15/19/23:05 | PT5M |
| `TraeDailyCheckin` | `trae auto --silent` | 09/13/15/19/21/23:05 | PT5M |

Trae 域是本仓库自己扩展的: 取证文档 `NOTE-01` 只覆盖 WorkBuddy,Qoder 见 `NOTE-02`。

### 2.1 触发时间与时限在哪改

**改 `config/domains/schedule.yaml` 的 `tasks`**,不用动代码:

```yaml
tasks:
  QoderDailyCredits:
    times: ["10:05", "11:05"]
    limit_minutes: 5
```

`data.py` 的清单只提供**默认值**: 未列进 YAML 的任务沿用默认值,所以只想改
一个任务时不必把整张表抄一遍。命令行(`argv`)不开放配置——它是代码结构,
不是可调参数。

时限与各域时间预算联动的下限由 `test_execution_limit_covers_domain_budget` 守着:
改小到低于预算会在测试期就红,不会等到线上被平台强杀才发现。

## 3. 三平台实现差异

| 平台 | 机制 | 多触发时间 | 执行时限 | 输出去向 |
|------|------|------------|----------|----------|
| Windows | 任务计划程序 XML + `schtasks /Create /XML` | 多个 `CalendarTrigger` | `ExecutionTimeLimit` | 任务历史 |
| macOS | `~/Library/LaunchAgents/*.plist` + `launchctl load` | `StartCalendarInterval` 数组 | **不支持**(忽略) | `StandardOutPath`/`StandardErrorPath` |
| Linux | 用户 crontab 的带标记块 | 每个时间一行 | **不支持**(忽略) | 由各域 `--silent` 写日志文件 |

时限在 launchd/cron 上没有对应项,选择忽略而不是伪造: 各域自身带时间预算,
超时由进程自己收尾,比被平台强杀更可控(强杀会丢掉"已领到的部分"的日志)。

## 4. 两条必须保持的不变量

1. **高密度触发 + 幂等领取 = 兜底单点失败**。触发时刻机器可能关机/断网/刚开机
   网络没就绪,一天只有一次机会的话撞上就等于当天断签。所以**不要"优化"掉
   看起来多余的触发时间**——领取接口幂等,多跑不会多领。
2. **平台的执行时限必须大于脚本自己的时间预算**。改任一侧都要同步改另一侧。

## 5. 踩过的坑(改动前必读)

- **schtasks 的 XML 必须是 UTF-16**,写 UTF-8 会报"XML 格式错误"。
- **`LogonType` 必须是 `InteractiveToken`,不能是 `InteractiveTokenOrPassword`**:
  后者要求 schtasks 存一份凭据,非交互注册时直接报"用户名或密码不正确"。
  代价是任务只在用户已登录的会话里跑——对签到场景正好,用户没登录时本来也
  读不到桌面端登录态。有回归测试守着。
- **schtasks 输出的是控制台 ANSI 编码**(中文系统即 GBK),按 UTF-8 解会得到一坨
  问号,连"用户名或密码不正确"这种关键提示都读不出来 → `common._decode()`。
- **命令行里的可执行文件必须是绝对路径**: 定时任务由系统进程拉起,没有登录
  shell 的 PATH,写相对命令名会静默失败(任务显示"已运行"但什么都没发生)。
- **crontab 用带标记块管理**,不裸追加行,否则 `uninstall` 无法只删自己的条目。
