# SPEC-03-workbuddy域

> **概要**: WorkBuddy 桌面端每日签到 + 成长中心(旅行/任务/补签/盲盒)的
> as-built 实现。
> **状态**: 已实现(本机真实验证过: 签到、连签、成长中心、积分兑换)
> **关联**: [SPEC-01](SPEC-01-架构总览.md) · [SPEC-02](SPEC-02-core层公共契约.md) ·
> 逆向母本 [NOTE-01-workbuddy域接口取证](../../notes/NOTE-01-workbuddy域接口取证.md)

## 1. 模块分工(13 文件)

| 文件 | 职责 |
|------|------|
| `cli.py` | 命令入口(下表),`_run` 统一 `AutoSignInKitError→Exit` |
| `service.py` | run_action 总装配: 配置→凭据→Net→签到+成长中心;预算解析 |
| `credentials.py` | 凭据文件发现(`WORKBUDDY_AUTH_FILE` 覆盖)+ 读取重试 |
| `session.py` | 会话校验/头组装/调运行时解密(`resolve_session`) |
| `runtime.py` | 定位 WorkBuddy 客户端运行时 exe(解密助手宿主) |
| `helper_js.py` | **原样**注入客户端运行时的解密 JS(禁改,E501 豁免) |
| `net.py` | `Net`: endpoint + 头 + budget 包装(core.httpclient) |
| `checkin.py` | 签到状态/领取 |
| `growth.py` | 成长中心: 旅行 + 任务(接单/领奖),GrowthState 状态机 |
| `rewards.py` | 补签(makeup)+ 兑换 + 活跃日历解析/今日活跃检查 |
| `extras.py` | 盲盒彩票 / Buddy 盲盒 / 展示;`run_growth` 串联成长中心 |
| `fields.py` | 报文字段常量 |
| `schema.py` | WorkbuddyDomainConfig(endpoint/budget_seconds) |

## 2. 命令面

| 命令 | 语义 |
|------|------|
| `workbuddy auto [--silent]` | 签到 + 成长中心(每日主入口;幂等) |
| `workbuddy poll` | 轮询(静默写日志,每轮必写) |
| `workbuddy growth` | 仅成长中心 |
| `workbuddy status` / `claim` / `all` | 调试拆解 |
| `workbuddy doctor` | 离线检查凭据格式与解密运行时 |
| `workbuddy active [--silent]` | 今日活跃度: 今天是否已完成一次对话(见 §8) |

## 3. 凭据与解密链

1. `credentials.find_auth_file`: `WORKBUDDY_AUTH_FILE` 优先(单独报告该路径,
   不给无关建议);否则候选目录探测。
2. 凭据为 **sym-v1 信封**(新版加密): 解密不逆向——`session.resolve_session`
   找到客户端运行时 exe(`runtime.find_workbuddy_runtime`,走 budget),
   经 `core.adapters.native_helper.run_json_stdin_helper` 把
   `helper_js.py`(与原项目逐行对齐的 JS 原文)喂给运行时,读取解密后的会话。
3. `session._validate_session` 结构校验 + `build_headers` 组装请求头;
   失败统一 `AuthError`(带 output DTO)。

> 这是"必须借客户端运行时"场景: method 在客户端里,我们只运送请求与收取结果。

## 4. 接口契约(逆向,来自分析母本)

宿主 `https://copilot.tencent.com`(schema 兜底,`auth.endpoint` 优先):

| 端点 | 动作 | 说明 |
|------|------|------|
| `/v2/billing/meter/checkin-activity-status` | 签到状态 | 连签天数/累计积分 |
| `/v2/billing/meter/daily-checkin` | 签到领取 | 已签幂等 |
| `/v2/activity/growth/*` | 成长中心 | 旅行/任务/礼物/兑换 等 7 步族 |

易错契约:
- 兑换 `tier` 传 `"7d"/"14d"/"28d"`;400 时**回落天数重试**一遍(rewards)。
- 接单 body `{"task_codes":[...]}`(growth)。
- 401/403、"天数不足"403、`no chance` 400 → **先于通用 auth 判定**做语义映射
  (growth/checkin 的 auth_failure_dto)。
- 写操作 post 默认 `retry=False`(net.py),防重复领取。

## 5. 预算(时间)

| 场景 | 默认 | 上限 |
|------|------|------|
| 签到(auto/silent) | 420s | 540s |
| 轮询(poll/轮询族) | 180s | 240s |

`WORKBUDDY_BUDGET_SECONDS` 优先 → 非法回落默认、越界夹上限;`parse_budget`
**绝不抛异常**(配置坏了也要能出结果)。对应计划任务执行上限 ≤ 600s(PT10M 减余量)。

## 6. 结果与退出码

- 主流程结果: `CLAIMED / ALREADY / ERROR / TIMEOUT / NETWORK / NO_SESSION / AUTH_*`。
- `emit_result` 用 workbuddy 语义: 非 silent 仅 `ERROR` 补写日志;
  日志文件 `workbuddy.log`(`WORKBUDDY_SIGNIN_LOG` 覆盖)。
- 轮询族结果每轮必写(与 auto 一致,记录即定时任务心跳)。
- 连签/累计积分等数值进 result 顶层字段(stdout 机器可读)。

## 7. 失效模式(维护时先看这里)

- 客户端升级改凭据格式 → doctor 的 runtime 能力探测会先红;补 helper_js 对齐。
- 表盘/端点漂移 → 逆向母本 + 抓包;更新本节和按 SPEC-01 §8 同步。
- 服务端窗口期调整 → 只动 budget/调度,不动契约。

## 8. 活跃保底(active)——活跃天数只认对话,没对话就自动补

`/v2/activity/growth/heatmap` 的 `score` 是当天的**活跃**记录: **只有完成过一次
对话才会记分,领取积分不计**。"领了积分但没对话"会让连续活跃天数静默中断。

`active` 是完整的保底链(`rewards.run_active` + `chat.py`):

1. 查 heatmap: `today`(服务端日期,不用本机时区)在 `cells` 里的 score,
   `>0` → `ACTIVE`(退出码 0,收工)
2. `==0` → **自动补一次对话**(POST `/v2/chat/completions`,`model=auto`,
   流式,话术最短化省额度;契约取证见 NOTE-01 §12)
3. 复查 heatmap 确认记分 → `ACTIVE`(带 `chatted: true`)或
   `NO_ACTIVITY` + `needs_attention: false`(对话已完成但日历未记分,视为
   可能的延迟,交下次调度确认——不算失败)
4. 补对话本身失败(401/403/业务码)→ 保留 `needs_attention`,报告带原因

结构异常(日期/分数格式错、同日冲突)直接抛 `ValueError`,**不推测为"未活跃"**
——猜错方向会误导用户白跑一趟。

调度任务 `WorkBuddyActiveCheck` 每天 12:05 / 20:05 各跑一轮(见 SPEC-06):
中午那次补对话后当天还来得及被人工看见,傍晚那次兜底上午的失败。
