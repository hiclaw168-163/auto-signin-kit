# workbuddy-auto-signin 功能与实现分析

> 逆向取证 · 对应 as-built: [SPEC-03-workbuddy域](../superpowers/specs/SPEC-03-workbuddy域.md)

> 分析对象：`D:\codes\opensoure\workbuddy-auto-signin`（github.com/88lin/workbuddy-auto-signin，MIT © 88lin）
> 分析日期：2026-10-21
> 用途：为后续在本仓库 `auto-signin-kit` 中复用/重写同类功能留档。

---

## 1. 项目一句话

两个**零依赖**（纯 Python 标准库）的自包含脚本，分别自动领取：

1. **WorkBuddy / 腾讯 AI 编程助手** 的每日签到积分 + 成长中心全套奖励（`signin.py`）
2. **Qoder CN** 的每日 100 通用 Credits（`qoder_signin.py`）

核心思路：**只读取本机桌面端登录后写出的凭据文件 → 解出 access token → 直接调用官方客户端使用的 HTTP 接口（从桌面端 `app.asar` 逆向得到）→ 幂等地领取每日奖励**。仓库不含任何密钥；`Authorization` 头和 token 永不打印、永不落盘。

---

## 2. 文件清单

| 文件 | 规模 | 作用 |
|---|---|---|
| `signin.py` | ~1800 行 | WorkBuddy 签到 + 成长中心主脚本（单文件自包含） |
| `qoder_signin.py` | ~900 行 | Qoder CN 每日 Credits 独立脚本 |
| `install-windows.ps1` | ~10KB | 一键注册 2 个 Windows 计划任务（签到 + 成长中心轮询） |
| `install-qoder-windows.ps1` | ~8KB | 注册 1 个 Qoder 领 Credits 计划任务 |
| `workbuddy-auto-signin.plist.example` | 4.7KB | macOS launchd 模板（3 种触发器） |
| `tests/test_*.py` | 6 个 | pytest 单元测试（离线、不联网） |
| `tests/fixtures/sym-v1.json` | | 加密凭据信封样例 |
| `docs/plans/*.md` | 2 份 | 凭据兼容方案 / 补登兼容规则的设计说明 |

---

## 3. signin.py（WorkBuddy 主脚本）

### 3.1 命令体系

```
python signin.py auto            # 签到 + 成长中心（每日主入口）
python signin.py silent          # 同 auto，但结果写 signin.log 而非 stdout（配合 pythonw 静默）
python signin.py growth          # 仅成长中心（不签到）
python signin.py silent-poll     # 轮询：补签（未签才签）+ 成长中心，空跑不写日志
python signin.py silent-growth   # silent-poll 的旧名（兼容已装的老任务）
python signin.py status          # 仅查签到状态（调试，输出原始返回）
python signin.py claim           # 仅领取签到（调试，幂等）
python signin.py all             # status + claim（调试）
python signin.py doctor          # 离线检查凭据格式与运行时能力，不解密、不联网
```

退出码：`0` 成功/已签/活动未开启；`1` 业务失败（含认证错误）；`2` 无凭据/参数错误。

### 3.2 凭据定位（find_auth_file）

环境变量 `WORKBUDDY_AUTH_FILE` 优先覆盖；否则按平台探测以下候选：

| 平台 | 路径 |
|---|---|
| Windows 桌面端 | `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info` |
| macOS 桌面端 | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| Linux（CodeBuddy CLI，无桌面端） | `$XDG_DATA_HOME/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info` |
| Linux 旧路径 / 兜底 | `~/.config/...`、`~/.workbuddy/auth/workbuddy-desktop.info` |

读取凭据带 3 次 × 2s 重试（客户端刷新 token 时会短暂独占文件锁，撞上 PermissionError 等等再读）。

### 3.3 凭据文件格式与鉴权

凭据 JSON 结构：`{ "auth": { accessToken, domain?, endpoint? }, "account": { uid, enterpriseId? } }`

- **accessToken** 两种形态：
  - 旧版：明文字符串（需匹配 `[A-Za-z0-9._~+/-]+=*`，长度 ≤ 32768）
  - 新版：`$wbEncrypted` 信封对象（见下）
- `endpoint` 若存在必须为 https，否则回退默认 `https://copilot.tencent.com`

**请求头构造（build_headers）**：

```
Accept: application/json
Authorization: Bearer <token>
Content-Type: application/json
X-User-Id: <uid>
User-Agent: WorkBuddy
X-Enterprise-Id / X-Tenant-Id: <enterpriseId>   （存在时）
X-Domain: <domain>                              （存在时）
```

### 3.4 加密凭据解密（↗ 重用价值高，处理很讲究）

支持且仅支持 `$wbEncrypted=1` + envelope `suite=1`（sym-v1 / suite 1）。

**实现方式**：把一段内嵌的 JS（`AUTH_HELPER_JS`，~100 行）通过 `node -e` / 客户端自带的 Electron 运行时在**隐藏子进程**里执行：

- stdin 传 `{version:1, operation:"decrypt", value:<信封对象>}`（固定正则校验，限制 65536B）
- 子进程内通过 Electron 内部绑定 `process._linkedBinding('electron_browser_workbuddy_storage')` 的 `loggerGet()` 拿到 `atRestSecretKey`
- `key = sha256(atRestSecretKey字符串)`；`keyId = sha256(key).hex 前 16 位` 用于校验匹配
- `AES-256-GCM`（nonce 12B、authTag 16B），AAD 按 `'WB-AAD\0' + [1] + len-prefix('WBEV1') + len-prefix('sym-v1') + [0,0,0,1] + len-prefix(keyId) + [2,0,0]` 拼接
- stdout 只回 `{version:1, ok:true, accessToken}`，明文 token 不缓存、不写盘；解密后 key/plaintext 缓冲区清零
- 10s 超时、输入/输出各 65536B 上限；`probe` 操作可离线探测运行时能力（`doctor` 用）

**错误语义（AuthError，绝不发送无效令牌）**：`INVALID_FORMAT / UNSUPPORTED_ENVELOPE / RUNTIME_NOT_FOUND / INVALID_RUNTIME_PATH / RUNTIME_UNAVAILABLE / KEY_MISMATCH / DECRYPT_FAILED / HELPER_TIMEOUT / HELPER_PROTOCOL`。

**客户端可执行文件定位（Windows）**：`WORKBUDDY_EXE` 环境变量 → 默认安装目录 → HKCU/HKLM 32/64 位卸载注册表项（路径可含空格、逗号）；macOS 探测 `.app/Contents/MacOS/`。只读注册表，不执行卸载命令。

### 3.5 签到 API 契约

Base：`https://copilot.tencent.com`（可被凭据里 `auth.endpoint` 覆盖）。

| # | 方法 | 路径 | 作用 | 幂等性 |
|---|---|---|---|---|
| 1 | POST | `/v2/billing/meter/checkin-activity-status` | 查活动状态：`active`、`today_checked_in`、`streak_days`、`total_credits` 等 | 只读 |
| 2 | POST | `/v2/billing/meter/daily-checkin` | 领取今日积分，成功返回 `{"credit":100}` | 幂等：已领返回 `null` 或 HTTP 400 `{"code":10001}` |

主流程（run_auto）：查状态 → 活动未开启按 `INACTIVE` 处理 → 今日已签按 `ALREADY` → 未签才领取 → 领取后再查一次状态取最新连胜/累计数 → 组装人话汇报。

### 3.6 成长中心（run_growth，核心特色）

Base 路径：`/v2/activity/growth`。七个子步骤按固定顺序，**每步独立 try，一段失败不影响其余**：

| 顺序 | 功能 | API | 关键参数/契约 |
|---|---|---|---|
| 1 | **Buddy 旅行** | `GET buddy/travel/status`、`POST buddy/travel/claim {record_id}`、`GET buddy/travel/config`、`POST buddy/travel/depart {location_id}` | 状态机 `arrived → idle → traveling`；`daily_limit_reached=true` 时直接收手（省掉 2 个请求）；只有领取成功后 state 才视为 idle 允许再派出 |
| 2 | **任务系统** | `GET /tasks`、`POST /tasks/accept {"task_codes":[...]}`（批量 ≤20）、`POST /tasks/{code}/claim`（body 空） | `accept_status: not_accepted/accepted/in_progress/completed/claimed`；接单复数数组，旧单数写法会 400；领奖优先读服务端实发字段 |
| 3 | **补登卡** | `GET /streak`、`GET /heatmap`、`POST makeup-cards/use {"target_date"}` | `makeup_cards` 兼容 `{balance:n}` 对象和纯数字两种形状；候选日 = 当月 + 今天之前 + 活动上线之后 + heatmap score==0，排除 `makeup_dates`；每轮最多 1 张；400「date is not broken」视为正常竞争 |
| 4 | **连登兑换** | `GET /redeem/summary`、`POST /redeem {"tier":"7d","client_token":...}` | 三档 `7d/14d/28d`（入门 7 / 进阶 14 / 巅峰 28 天）；tier 必须是档位标识字符串，被判 unknown 时退回天数重试；403「天数不足」要在 401/403 通用检查**之前**独立处理，否则会误杀整轮；实发字段带 `_granted` 后缀 |
| 5 | **盲盒抽奖** | `GET /lottery/chances`、`POST /lottery/draw {"client_token"}` | 缺 `client_token` 必 400；一轮只开一次（写路径不可逆）；`need_address` 实物奖提醒用户去填收件信息（脚本绝不代填） |
| 6 | **Buddy 盲盒** | `GET /buddy/quota`、`POST /buddy/open {"count":N,"client_token"}` | `count = min(affordable, max_open_count)` |
| 7 | **展示值** | `GET /energy`、复用第 3 步的 `/streak` | 纯展示（能量余额、连签天数），预算不够直接不取、不计失败 |

步骤间有**显式顺序理由**：任务奖放在抽奖前（任务送的抽奖机会/能量马上能用）；补登放在兑换前（补登会改变连登天数，先补才能拿到最新解锁状态）。

### 3.7 可靠性设计（↗ 这套机制整体值得搬走）

**幂等优先**：先查状态、未签才领；重复运行不多领。所有领取接口当天重复调用只返回「已领」。

**读写分离的重试策略**：
- 只允许重试两类失败：网络不可达（退避 5/15/30/60/90s）、服务端 5xx（退避 3/10s）
- **写操作（补登/抽奖/领奖/兑换）默认不重试**——超时若发生在服务端处理完成之后，重试会重复提交
- 例外：两个签到接口可重试（状态查询只读；领取接口本身幂等）

**时间预算（防被计划任务强杀**）：
- 伪码 `CODE_BUDGET_OUT=-2` 表示「没发出去，再发就要超出任务时限」
- 签到任务预算默认 420s / 上限 540s（对应 pt10M 时限留 60s 余量）；轮询 180/240（PT5M）
- Windows 上须 ` ExecutionTimeLimit > WORKBUDDY_BUDGET_SECONDS`，非法值自动夹到安全区间并附 `config_warning`
- 预算耗尽主动收尾，**已领到的部分照常写日志**，剩余项下次再领

**输出契约（emit）**：一行 JSON；`silent` 前缀命令写日志文件（`[时间戳] {json}`），非 silent 打 stdout；`ERROR` 结果**两条通道都走**（防计划任务命令拼错时无痕消失）；自定义日志路径不可写时回退脚本同目录；`_sensitive_values` 集合做 token 脱敏替换为 `[REDACTED]`。

**轮询静默策略**：`silent-poll` 空跑（已签 + 成长中心无可领取项）**不写日志**，避免有价值的记录被淹没；`WORKBUDDY_GROWTH_LOG_EMPTY=1` 可强制逐轮落盘。

**结果码体系**（`result` 字段 + `needs_attention` 标志）：

| result | 含义 | 退出码 |
|---|---|---|
| `CLAIMED` / `ALREADY` / `INACTIVE` / `GROWTH` | 正常业务状态 | 0 |
| `NO_AUTH` / `NO_SESSION` | 本地凭据缺失 | 2 / 1 |
| `AUTH_ERROR`（带 reason）/ `AUTH_REJECTED`（401）/ `FORBIDDEN`（403） | 认证问题，需人工介入 | 1 |
| `NETWORK` / `TIMEOUT` | 环境/时限问题，下次自动重试 | 1 |
| `ERROR` / `UNKNOWN` | 其他失败 | 1 / 2 |

已识别的正常业务态（旅行名额用完、档位未解锁、日期无需补登）**不算**硬失败；硬失败需 `needs_attention:true` 且非零退出码。

### 3.8 配置环境变量

| 变量 | 作用 |
|---|---|
| `WORKBUDDY_AUTH_FILE` | 手动指定凭据文件 |
| `WORKBUDDY_EXE` | 指定客户端可执行文件（加密凭据解密用） |
| `WORKBUDDY_SIGNIN_LOG` | silent 日志路径（默认脚本同目录 `signin.log`） |
| `WORKBUDDY_BUDGET_SECONDS` | 网络请求时间预算 |
| `WORKBUDDY_GROWTH_LOG_EMPTY=1` | 空跑也写日志 |

---

## 4. qoder_signin.py（Qoder CN）

独立的第二个脚本，与 WorkBuddy 无任何耦合。

### 4.1 API 契约

Base：`https://openapi.qoder.com.cn`（`QODER_BASE_URL` 可覆盖）。

```
GET  /sash/api/v1/me/campaigns            查活动列表
POST /sash/api/v1/me/campaigns/{id}/claim 领取（幂等，重复调用返回 replayed:true）
```

必需请求头：`Accept / User-Agent: Qoder/claim / Cosy-ClientType: 10 / Authorization: Bearer <token>`（缺 `Cosy-ClientType` 服务端返回空活动列表）。

活动筛选逻辑（select_campaigns）：`actionType == "CLAIM_BENEFIT"` → 时间窗内（`startAt/endAt`）→ 按 `claimStatus` 分流：`CLAIMABLE` 待领 / `CLAIMED` 已领 / 其他 `PENDING` / 窗外 `INACTIVE`。奖励量从 `benefit.amount` 读，有效期 `benefit.validity.mode == RELATIVE_DAYS`（30 天）。

### 4.2 Windows 本地登录态解密

- 数据目录：`%APPDATA%\com.qodercn.app*`（glob 扫描，`QODER_AUTH_DIR` 可覆盖）
- 加密文件：`auth.v1.dat`，密钥在 `Local State` 文件里，由 **DPAPI（CryptUnprotectData）+ AES-256-GCM（CNG BCrypt，ctypes 纯系统调用；装了 `cryptography` 则优先用）** 双层保护
- token 过期：`expiresAt` ISO 时间戳，过期直接报 `AUTH_REJECTED` 提醒重新登录（**脚本不碰 refresh token**，不与桌面端争抢刷新令牌轮换）
- 非 Windows：`QODER_TOKEN` 环境变量手动提供（优先级高于本地登录态）

### 4.3 设备风控头（risk headers）

服务端对**带设备标识**的请求才下发活动列表。脚本不编造标识，而是运行 Qoder 安装目录里自带的 `resources/umid/runtime-info.exe` 取客户端平时用的那一份：

- 定位顺序：注册表卸载项扫 `qoder` 关键字 → 进程快照（CreateToolhelp32Snapshot）找运行中的 `qoder cn.exe` 提取路径 → 默认目录
- 找不到组件就退回普通请求，不影响主流程；`QODER_UMID_EXE` 可手动指定；结果带缓存

### 4.4 命令与输出

```
python qoder_signin.py doctor          # 离线检查登录态和设备组件
python qoder_signin.py status          # 只查活动列表不领取
python qoder_signin.py auto --dry-run  # 只报告会领到什么
python qoder_signin.py auto            # 真领取（幂等）；--once 跳过网络重试
```

结果码：`CLAIMED / ALREADY / PENDING / INACTIVE / DRY_RUN / NO_AUTH(2) / AUTH_ERROR / AUTH_REJECTED / FORBIDDEN / NETWORK / TIMEOUT / ERROR`。重试仅针对网络错误 / 429 / 5xx（退避 5/15/30s），时间预算 240s。空跑不写日志（`QODER_SIGNIN_LOG_EMPTY=1` 例外），日志默认 `qoder_signin.log`。

---

## 5. 定时自动化（三种模式）

| 模式 | 平台 | 原理 | 成本 |
|---|---|---|---|
| A（AI 自动化） | 全平台 | WorkBuddy 自动化定时触发 → AI 模型跑脚本并汇报 | 每次消耗一次模型调用 |
| B（系统级静默，推荐） | Win/macOS | 任务计划程序 / launchd 直接跑脚本，`silent` 写日志 | 零 |
| Qoder | 仅 Windows | 独立计划任务 | 零 |

**Windows（install-windows.ps1）**：自动探测 `pythonw.exe`（PATH → python.exe 同目录 → glob → py 启动器），注册 2 个任务后**校验**已注册并成功（任一失败报告出错阶段并退出码 1）：

| 任务 | 触发 | 参数 | 时限 |
|---|---|---|---|
| `WorkBuddyAutoSignin` | 每天 00:05 | `signin.py silent` | PT10M |
| `WorkBuddyGrowthPoll` | 每天 01:05/05:05/09:05/13:05/17:05/21:05 | `signin.py silent-poll` | PT5M |

**轮询兼做签到兜底**：每次轮询先查签到状态、未签才签（幂等，已签只是一个查询请求）→ 00:05 撞上关机/断网不再等于断签，一天最多 7 次机会。

**Qoder（install-qoder-windows.ps1）**：单任务 `QoderDailyCredits`，每天 10:05 / 11:05 / 15:05 / 19:05 / 23:05 五次触发（分摊「10:00 刚刷新还没下发」「刚开机网络没就绪」的风险），PT5M，与 WorkBuddy 任务互不影响。

**macOS（plist.example）**：一个 launchd 代理内置 3 种触发（每天 00:05、每 4 小时、登录时），全部跑 `auto`；日志 `/tmp/workbuddy-auto-signin.out/.err`。两个已知坑：python3 路径必须用 `which` 实测（`/usr/bin/python3` 可能是占位壳）；脚本不能放在 TCC 保护目录（Documents/Desktop/Downloads）。

---

## 6. 工程/测试

- **依赖策略**：标准库 only（`urllib/ssl/json/ctypes`...），加密凭据解密 Windows 用 ctypes 调 DPAPI/BCrypt
- **测试**：pytest 全离线（mock 网络层），覆盖鉴权（`test_auth`）、成长中心契约（`test_growth`）、运行时发现（`test_runtime_discovery`）、Qoder（`test_qoder_signin`）、两个安装脚本（各一个测试文件）
- **docs/plans**：两份设计文档（凭据兼容方案、补登规则兼容）记录了接口逆向的验证依据
- **安全设计**：token 只进内存不序列化；`emit` 输出前统一正则脱敏；永不打印 Authorization 头；秘钥子进程字节上限 + 超时双保险；不改写原凭据文件

---

## 7. 复用要点清单（后续在本仓库实现时的 checklist）

1. **凭据层**：平台化探测凭据文件 → 支持明文/加密两种 token 形态 → 加密态走客户端子进程解密（不信任正则外的东西、只过内存管道）→ 明确的 `AuthError` reason 枚举
2. **请求层**：统一 `_request`（urllib + ssl 默认上下文）→ GET/5xx 重试、**写操作不重试** → 伪 HTTP 码（-1 网络 / -2 预算）区分「没拿到响应」
3. **时间预算**：单调时钟预算 → 请求前检查剩余预算 → 伪码 `BUDGET_OUT` → emit 兜底写日志保证「必有痕迹」
4. **业务层**：幂等查询先行（status / chances / quota）→ 按服务端状态字段分流（claimed/claimable/locked/limit）→ 正常业务态与硬失败严格分离 → `needs_attention` 仅对需人工介入的失败置位
5. **输出层**：一行 JSON + `report` 人话字段 + 脱敏 → silent 写日志 / interactive 打 stdout → 双通道防黑洞
6. **调度层**：高密度触发（轮询）+ 幂等领取 = 兜底单点失败；计划任务 `ExecutionTimeLimit` 必须大于脚本预算且常数联动
7. **测试**：把 API 契约的具体字段（如 `accept_status` 枚举、`_granted` 后缀、`{balance:n}` 双形状）做成离线测试 fixture —— 这些细节服务端一改版就是坑

## 8. 许可

原项目 MIT License © 88lin。复用其代码时需保留许可声明；接口系从桌面客户端逆向获得，可能随客户端改版失效。

---

### 附：结果查询接口速查（signin.py 成长中心完整 API 清单）

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
         GET  /v2/activity/growth/heatmap
         POST /v2/activity/growth/makeup-cards/use        {target_date}
         GET  /v2/activity/growth/redeem/summary
         POST /v2/activity/growth/redeem                  {tier:"7d",client_token}
         GET  /v2/activity/growth/lottery/chances
         POST /v2/activity/growth/lottery/draw            {client_token}
         GET  /v2/activity/growth/buddy/quota
         POST /v2/activity/growth/buddy/open              {count,client_token}
         GET  /v2/activity/growth/energy
Qoder：  GET  https://openapi.qoder.com.cn/sash/api/v1/me/campaigns
         POST /sash/api/v1/me/campaigns/{campaignId}/claim
```
