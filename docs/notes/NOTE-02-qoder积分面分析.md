# Qoder CN 积分面分析(2026-10-21,客户端 0.4.8)

> 逆向取证 · 对应 as-built: [SPEC-04-qoder域](../superpowers/specs/SPEC-04-qoder域.md)

> 结论先行:**Qoder CN 没有"宠物/旅行/抽奖"这类可脚本化的第二积分入口**。
> 桌面端"宠物"是纯 UI 玩偶(Q 达鸭),与积分无任何关联;积分获取的服务端
> 入口只有 campaigns 一条链路,我们的 `qoder auto` 已经完整覆盖。

## 调查方法

| 证据源 | 做法 |
|---|---|
| 桌面端安装目录 `D:\software\ai-tools\Qoder CN\resources\app.asar`(157.8 MB) | 字符串/UTF-8 中文关键词全量扫描 |
| Agent 运行时 `app.asar.unpacked\...\qoder-worker-runtime.obf.mjs` | 同上 |
| VS Code 扩展 (`qoder.sites` / `qoder.knowledge.center`) | 同上 |
| 桌面端 webview 的 V8 代码缓存 (`%APPDATA%\com.qodercn.app.stable\Code Cache\js`) | 二进制扫描 + 端点正则 |
| **活动页本体**(服务端实时下发) | `campaignUrl → https://openapi.qoder.com.cn/growth-page/activity-iframe`,抓取 HTML + 引用的阿里 CDN JS(`g.alicdn.com/qbase/qoder/0.0.730/growth-page/activity-iframe/activity-iframe.js`,272 KB)分析 |
| 服务端 | 用已解密登录态实际调用接口验证 |

## 发现的完整 `/sash/api` 端点面

客户端全部代码(asar + 运行时 + 扩展 + webview 缓存 + 活动页 JS)里出现的
sash 端点**就这五个**:

| 端点 | 作用 | 对积分的意义 |
|---|---|---|
| `GET /sash/api/v1/me/campaigns` | 活动列表(uid/showCampaign/claimable/campaignUrl/campaigns) | ✅ 已实现 |
| `POST /sash/api/v1/me/campaigns/{id}/claim` | 领取(幂等) | ✅ 已实现 |
| `/sash/api/v1/me/notifications/`(带 id 后缀) | 站内通知 | ❌ 试探 404,无领取语义 |
| `/sash/api/v2/me/usage` | 用量/配额查询 | 只读,无领奖 |
| `/sash/api/v1/me/integrations/github/repo-access`、`/organizations/` | 集成授权 | 与积分无关 |

活动页 iframe 的 JS 里出现的埋点全部围绕 campaign 一个部件:
`campaign_claim_clicked / campaign_claim_succeeded / campaign_claim_failed /
campaign_retry_clicked ...` —— 没有签到、任务、抽奖、宠物互动等任何其他领取流程。
(代码里的 `draw*` 命中全是 QR 码绘制函数,d不是"抽奖"。)

## "宠物"是什么

asar 中"宠物"字样出现 33 处,**全部**是桌面宠物设置页的无障碍文案/文案键
(`desktopPet.showPet`、"当前宿主仅支持内置的 Q 达鸭"、"Qoder 桌面宠物:
空闲/正在工作/需要你的操作")。它是把 AI 助手拟人化挂件在桌面上,无接口写入、
无积分、无任务。WorkBuddy 的 Buddy 系统(签到送能量 + travel + 礼物盒 +
任务 + 补签卡 + 连登兑换 + 盲盒抽奖)在 Qoder CN 里**没有对应物**。

## campaigns 的运作模式

`act-20261020-791` 这类活动 key 说明 campaign 是**服务端按用户行为/时间窗
下发的一次性领奖**,payload 顶层 `claimable` 已从 true 变 false(今天领完)
。没有客户端可触发的"多做任务多领"端点;窗口外 `INACTIVE`。

## 对本仓库的影响

- `domains/qoder` 当前已是最优覆盖(campaigns + claim + 风控头 + 幂等)。
- 未来若有新积分玩法,会先出现在 `openapi.qoder.com.cn/growth-page/*` 下的
  新 iframe 页面;届时按本文档的方法抓 `g.alicdn.com/qbase/qoder/<新版本>/`
  对照分析即可。
- `trae` 域逆向时可直接复用本文档的方法(定位 webview 缓存 →
  `campaignUrl` → 拉活动页 JS 分析端点)。

---

## 附: 凭据、风控头与命令的取证

上一节证明的是"积分入口只有 campaigns";这一节记录**拿到这些端点后怎么用**
的事实: 凭据怎么解、为什么必须带风控头、命令面怎么切。

### A1. campaigns 契约

Base:`https://openapi.qoder.com.cn`(`QODER_BASE_URL` 可覆盖)。

```
GET  /sash/api/v1/me/campaigns            查活动列表
POST /sash/api/v1/me/campaigns/{id}/claim 领取(幂等,重复调用返回 replayed:true)
```

必需请求头:`Accept` / `User-Agent: Qoder/claim` / `Cosy-ClientType: 10` /
`Authorization: Bearer <token>`——**缺 `Cosy-ClientType` 服务端返回空活动列表**,
表现为"没有可领的活动"而不是报错,很容易误判成活动真的没了。

筛选逻辑: `actionType == "CLAIM_BENEFIT"` → 时间窗内(`startAt/endAt`)→ 按
`claimStatus` 分流: `CLAIMABLE` 待领 / `CLAIMED` 已领 / 其它 `PENDING` / 窗外
`INACTIVE`。奖励量读 `benefit.amount`,有效期 `benefit.validity.mode == RELATIVE_DAYS`(30 天)。

### A2. Windows 本地登录态解密

- 数据目录: `%APPDATA%\com.qodercn.app*`(glob 扫描,`QODER_AUTH_DIR` 可覆盖)
- 加密文件 `auth.v1.dat`,密钥在 `Local State` 里,由 **DPAPI(CryptUnprotectData)
  + AES-256-GCM(CNG BCrypt)** 双层保护
- token 过期: `expiresAt` ISO 时间戳,过期直接报 `AUTH_REJECTED` 提醒重新登录——
  **不碰 refresh token**,不与桌面端争抢令牌轮换
- 非 Windows: `QODER_TOKEN` 环境变量手动提供(优先级高于本地登录态)

### A3. 设备风控头

服务端对**带设备标识**的请求才下发活动列表。本项目不编造标识,而是运行 Qoder
安装目录里自带的 `resources/umid/runtime-info.exe` 取客户端平时用的那一份:

- 定位顺序: 注册表卸载项扫 `qoder` 关键字 → 进程快照找运行中的 `qoder cn.exe`
  提取路径 → 默认目录
- 找不到就退回普通请求,不影响主流程;`QODER_UMID_EXE` 可手动指定;结果带缓存

### A4. 命令面与结果码

```
qoder doctor          # 离线检查登录态与设备组件
qoder status          # 只查活动列表不领取
qoder auto --dry-run  # 只报告会领到什么
qoder auto            # 真领取(幂等);--once 跳过网络重试
```

结果码: `CLAIMED / ALREADY / PENDING / INACTIVE / DRY_RUN / NO_AUTH(2) /
AUTH_ERROR / AUTH_REJECTED / FORBIDDEN / NETWORK / TIMEOUT / ERROR`。
重试仅针对网络错误 / 429 / 5xx(退避 5/15/30s),时间预算 240s;空跑不写日志
(`QODER_SIGNIN_LOG_EMPTY=1` 例外)。
