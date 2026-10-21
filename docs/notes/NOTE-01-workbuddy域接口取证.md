# workbuddy 域接口逆向取证

> 逆向取证 · 对应 as-built: [SPEC-03-workbuddy域](../superpowers/specs/SPEC-03-workbuddy域.md)
> 取证对象: WorkBuddy 桌面端(CodeBuddyExtension)写出的本地凭据,以及它调用的服务端 HTTP 接口
> 取证日期: 2026-10-21

本文档记录的是**客户端与服务端的事实**: 凭据放在哪、信封怎么解、端点长什么样、
哪些返回要当正常业务态处理。它不是本项目的代码设计——代码现状见 SPEC-03。

各节末尾的「→ 对应实现」指向本项目里落地这些事实的模块,方便对着改代码。

---

## 1. 取证对象与边界

链路是: 读取桌面端**登录后**写出的凭据文件 → 解出 access token → 直接调用官方
客户端使用的 HTTP 接口 → **幂等地**领取每日奖励。

边界(本项目不越过的线):

- 不代用户登录;凭据必须是客户端已经写出来的
- 不碰 refresh token,不与桌面端争抢令牌轮换
- 不改写原凭据文件;token 只进内存、不序列化、不落盘
- 输出前统一脱敏,`Authorization` 头与 token 永不打印

---

## 2. 凭据在哪

→ 对应实现: `domains/workbuddy/credentials.py`

环境变量 `WORKBUDDY_AUTH_FILE` 优先覆盖;否则按平台探测:

| 平台 | 路径 |
|---|---|
| Windows 桌面端 | `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info` |
| macOS 桌面端 | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| Linux(CodeBuddy CLI,无桌面端) | `$XDG_DATA_HOME/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info` |
| Linux 旧路径 / 兜底 | `~/.config/...`、`~/.workbuddy/auth/workbuddy-desktop.info` |

读取带 **3 次 × 2s 重试**: 客户端刷新 token 时会短暂独占文件锁,撞上
`PermissionError` 要等等再读。

---

## 3. 凭据文件结构与鉴权头

→ 对应实现: `domains/workbuddy/session.py`

文件 JSON:`{ "auth": { accessToken, domain?, endpoint? }, "account": { uid, enterpriseId? } }`

`accessToken` 两种形态:

- 旧版明文字符串(需匹配 `[A-Za-z0-9._~+/-]+=*`,长度 ≤ 32768)
- 新版 `$wbEncrypted` 信封对象(见 §4)

`endpoint` 若存在必须为 https,否则回退默认 `https://copilot.tencent.com`。

请求头构造:

```
Accept: application/json
Authorization: Bearer <token>
Content-Type: application/json
X-User-Id: <uid>
User-Agent: WorkBuddy
X-Enterprise-Id / X-Tenant-Id: <enterpriseId>   （存在时）
X-Domain: <domain>                              （存在时）
```

---

## 4. 加密凭据信封(sym-v1)

→ 对应实现: `domains/workbuddy/helper_js.py`(JS **原样注入,禁改**)、
`runtime.py`(定位客户端运行时)、`core/adapters/native_helper.py`(JSON-stdin 子进程)

支持且仅支持 `$wbEncrypted=1` + envelope `suite=1`。

解密方式: 把一段内嵌 JS 交给客户端自带的 Electron 运行时,在**隐藏子进程**里执行。

- stdin 传 `{version:1, operation:"decrypt", value:<信封对象>}`(固定正则校验,限 65536B)
- 子进程内通过 Electron 内部绑定
  `process._linkedBinding('electron_browser_workbuddy_storage')` 的 `loggerGet()`
  拿到 `atRestSecretKey`
- `key = sha256(atRestSecretKey字符串)`;`keyId = sha256(key).hex 前 16 位` 用于校验匹配
- `AES-256-GCM`(nonce 12B、authTag 16B),AAD 按
  `'WB-AAD\0' + [1] + len-prefix('WBEV1') + len-prefix('sym-v1') + [0,0,0,1] + len-prefix(keyId) + [2,0,0]`
  拼接
- stdout 只回 `{version:1, ok:true, accessToken}`;解密后 key/plaintext 缓冲区清零
- 10s 超时、输入输出各 65536B 上限;`probe` 操作可离线探测运行时能力(`doctor` 用)

**错误语义**(AuthError,绝不发送无效令牌):
`INVALID_FORMAT / UNSUPPORTED_ENVELOPE / RUNTIME_NOT_FOUND / INVALID_RUNTIME_PATH /
RUNTIME_UNAVAILABLE / KEY_MISMATCH / DECRYPT_FAILED / HELPER_TIMEOUT / HELPER_PROTOCOL`

**客户端可执行文件定位(Windows)**: `WORKBUDDY_EXE` → 默认安装目录 →
HKCU/HKLM 32/64 位卸载注册表项(路径可含空格、逗号);macOS 探测
`.app/Contents/MacOS/`。只读注册表,不执行卸载命令。

---

## 5. 签到接口

→ 对应实现: `domains/workbuddy/checkin.py`

Base:`https://copilot.tencent.com`(可被凭据里 `auth.endpoint` 覆盖)。

| # | 方法 | 路径 | 作用 | 幂等性 |
|---|---|---|---|---|
| 1 | POST | `/v2/billing/meter/checkin-activity-status` | 查活动状态:`active`、`today_checked_in`、`streak_days`、`total_credits` | 只读 |
| 2 | POST | `/v2/billing/meter/daily-checkin` | 领取今日积分,成功返回 `{"credit":100}` | 已领返回 `null` 或 HTTP 400 `{"code":10001}` |

主流程: 查状态 → 活动未开启按 `INACTIVE` → 今日已签按 `ALREADY` → 未签才领 →
领完再查一次状态取最新连签/累计数。

---

## 6. 成长中心七步

→ 对应实现: `growth.py`(旅行 + 任务)、`rewards.py`(补登 + 兑换 + 活跃日历)、
`extras.py`(盲盒 + 展示值,并串联整轮)

Base 路径:`/v2/activity/growth`。七步按固定顺序,**每步独立 try,一段失败不影响其余**:

| 顺序 | 功能 | API | 关键参数/契约 |
|---|---|---|---|
| 1 | **Buddy 旅行** | `GET buddy/travel/status`、`POST buddy/travel/claim {record_id}`、`GET buddy/travel/config`、`POST buddy/travel/depart {location_id}` | 状态机 `arrived → idle → traveling`;`daily_limit_reached=true` 时直接收手(省 2 个请求);只有领取成功后 state 才视为 idle 允许再派出 |
| 2 | **任务系统** | `GET /tasks`、`POST /tasks/accept {"task_codes":[...]}`(批量 ≤20)、`POST /tasks/{code}/claim`(body 空) | `accept_status: not_accepted/accepted/in_progress/completed/claimed`;接单是**复数数组**,单数写法会 400;领奖优先读服务端实发字段 |
| 3 | **补登卡** | `GET /streak`、`GET /heatmap`、`POST makeup-cards/use {"target_date"}` | `makeup_cards` 兼容 `{balance:n}` 对象与纯数字两种形状;候选日 = 当月 + 今天之前 + 活动上线之后 + heatmap `score==0`,排除 `makeup_dates`;每轮最多 1 张;400「date is not broken」视为正常竞争 |
| 4 | **连登兑换** | `GET /redeem/summary`、`POST /redeem {"tier":"7d","client_token":...}` | 三档 `7d/14d/28d`(入门 7 / 进阶 14 / 巅峰 28 天);tier 必须是档位标识字符串,被判 unknown 时退回天数重试;403「天数不足」要在 401/403 通用检查**之前**独立处理,否则会误杀整轮;实发字段带 `_granted` 后缀 |
| 5 | **盲盒抽奖** | `GET /lottery/chances`、`POST /lottery/draw {"client_token"}` | 缺 `client_token` 必 400;一轮只开一次(写路径不可逆);`need_address` 实物奖提醒用户去填收件信息(**脚本绝不代填**) |
| 6 | **Buddy 盲盒** | `GET /buddy/quota`、`POST /buddy/open {"count":N,"client_token"}` | `count = min(affordable, max_open_count)` |
| 7 | **展示值** | `GET /energy`、复用第 3 步的 `/streak` | 纯展示(能量余额、连签天数),预算不够直接不取、不计失败 |

步骤顺序不是随意的: **任务奖放在抽奖前**(任务送的抽奖机会/能量马上能用);
**补登放在兑换前**(补登会改变连登天数,先补才能拿到最新解锁状态)。

---

## 7. 活跃日历: 活跃天数只认对话

→ 对应实现: `rewards.heatmap_scores` / `rewards.today_activity`,命令 `workbuddy active`

`GET /heatmap` 的 `score` 是当天的**活跃**记录: **只有完成过一次对话才会记分,
领取积分不计**。所以"领了积分但没对话"当天仍算断,活跃天数会静默中断。

判定取 `today`(**服务端日期**,不用本机时区——本机时区会把月初/午夜算错一天)
在 `cells` 里的 score:`>0` 已活跃,`==0` 未活跃。

这是本项目单独做了一条命令的原因: 把它从"断档后才发现"变成"当天就能看见"。

---

## 8. 服务端语义与易错契约

改动这些地方前先回看本节,每一条都是踩过或验证过的:

- 枚举值写死在服务端: `accept_status` 五个值、`travel` 状态机三态、兑换三档
  `7d/14d/28d`。新增档位要先确认服务端标识
- 同一字段两种形状: `makeup_cards` 既可能是 `{"balance": n}` 也可能是纯数字
- `tier` 被判 unknown 时**退回天数重试一遍**;403「天数不足」必须**先于**通用
  401/403 判定处理,否则整轮被误杀
- 写路径(补登/抽奖/开盒/兑换)**不可逆**,重复调用不是幂等的,所以默认不重试
- `client_token` 缺失必然 400,构造请求时别漏
- 实发字段带 `_granted` 后缀,领奖结果优先读服务端实发值而不是本地推算
- 已识别的正常业务态(旅行名额用完、档位未解锁、日期无需补登)**不算**硬失败,
  不该置 `needs_attention`

---

## 9. 可靠性约定

→ 对应实现: `core/httpclient.py`(预算 + 分桶退避)、`core/report.py`(一行 JSON + 脱敏)

**幂等优先**: 先查状态、未签才领;重复运行不多领。

**读写分离的重试**:

- 只重试两类: 网络不可达(退避 5/15/30/60/90s)、服务端 5xx(退避 3/10s)
- **写操作默认不重试**——超时若发生在服务端处理完成后,重试会重复提交
- 例外: 两个签到接口可重试(状态查询只读,领取本身幂等)

**时间预算**(防被定时任务强杀):

- 伪码 `CODE_BUDGET_OUT=-2` 表示"没发出去,再发就要超出任务时限"
- 签到 420s / 上限 540s(对应 PT10M 留 60s 余量);轮询 180/240(PT5M)
- 平台上须 `ExecutionTimeLimit > WORKBUDDY_BUDGET_SECONDS`;非法值夹回安全区间
  并附 `config_warning`
- 预算耗尽主动收尾,**已领到的部分照常写日志**,剩余项下次再领

**输出契约**: 一行 JSON(stdout 给机器,过程日志走 stderr);`--silent` 写日志文件;
`ERROR` 结果**两条通道都走**(防任务命令拼错时无痕消失);敏感值统一替换为
`[REDACTED]`。

**结果码体系**:

| result | 含义 | 退出码 |
|---|---|---|
| `CLAIMED` / `ALREADY` / `INACTIVE` / `GROWTH` | 正常业务状态 | 0 |
| `NO_AUTH` / `NO_SESSION` | 本地凭据缺失 | 2 / 1 |
| `AUTH_ERROR`(带 reason)/ `AUTH_REJECTED`(401)/ `FORBIDDEN`(403) | 认证问题,需人工介入 | 1 |
| `NETWORK` / `TIMEOUT` | 环境/时限问题,下次自动重试 | 1 |
| `ERROR` / `UNKNOWN` | 其他失败 | 1 / 2 |

---

## 10. 环境变量

| 变量 | 作用 |
|---|---|
| `WORKBUDDY_AUTH_FILE` | 手动指定凭据文件 |
| `WORKBUDDY_EXE` | 指定客户端可执行文件(加密凭据解密用) |
| `WORKBUDDY_SIGNIN_LOG` | silent 日志路径(默认平台日志目录 `signin.log`) |
| `WORKBUDDY_BUDGET_SECONDS` | 网络请求时间预算 |
| `WORKBUDDY_GROWTH_LOG_EMPTY=1` | 空跑也写日志 |

---

## 11. 定时调度的两条依据

→ 对应实现: `domains/schedule/`(见 [SPEC-06](../superpowers/specs/SPEC-06-schedule域.md))

1. **高密度触发 + 幂等领取 = 兜底单点失败**。触发时刻机器可能关机/断网/刚开机
   网络没就绪,一天只给一次机会撞上就等于断签;而领取接口幂等,多跑不会多领。
   所以**不要"优化"掉看起来多余的触发时间**。
2. **平台执行时限必须大于脚本自己的时间预算**,且两侧常数联动。改任一侧都要
   同步改另一侧(有测试 `test_execution_limit_covers_domain_budget` 守着)。

轮询顺带做签到兜底: 每次轮询先查状态、未签才签(幂等),等于把签到的一天一次
机会变成多次。

---

## 12. 失效与重新取证

接口系从桌面客户端逆向获得,**可能随客户端改版失效**。症状通常是某一步开始稳定
返回 400/404,或字段形状变了(服务端改版最爱动的就是这种细节)。

重新取证的方法: 抓桌面客户端的请求(app.asar 里搜端点字符串,或对客户端进程
抓包),拿到新端点/新字段后**当次更新本文档**,并把新形状做成离线测试 fixture——
§8 列的那些细节,服务端一改版就是坑。

---

## 附: 端点速查

```
签到：   POST /v2/billing/meter/checkin-activity-status
         POST /v2/billing/meter/daily-checkin
成长：   GET  /v2/activity/growth/buddy/travel/status
         POST /v2/activity/growth/buddy/travel/claim      {record_id}
         GET  /v2/activity/growth/buddy/travel/config
         POST /v2/activity/growth/buddy/travel/depart     {location_id}
         GET  /v2/activity/growth/tasks
         POST /v2/activity/growth/tasks/accept            {task_codes:[...]}
         POST /v2/activity/growth/tasks/{task_code}/claim {}   ← 路径带 code
         GET  /v2/activity/growth/streak
         GET  /v2/activity/growth/heatmap                 ← 活跃日历(活跃天数只认对话)
         POST /v2/activity/growth/makeup-cards/use        {target_date}
         GET  /v2/activity/growth/redeem/summary
         POST /v2/activity/growth/redeem                  {tier:"7d",client_token}
         GET  /v2/activity/growth/lottery/chances
         POST /v2/activity/growth/lottery/draw            {client_token}
         GET  /v2/activity/growth/buddy/quota
         POST /v2/activity/growth/buddy/open              {count,client_token}
         GET  /v2/activity/growth/energy
```
