# NOTE-05: Qoder 国际版(mac)签到取证(2026-11-05,同日二次修正)

> 结论先行(修正版):**国际版有每日签到活动,mac 领不到的根因是风控头缺失**。
> 服务端只对带完整设备风控头(`Cosy-Machine*` 系列)的请求下发每日 100 Credits
> 活动;本仓库 `risk_headers` 此前是 Windows 专用,mac 上被 `IS_WINDOWS` 门禁
> 跳过,导致每日活动在 API 响应里"隐身"。无需新建 `qoder_intl` 域的结论不变。
>
> 第一版曾误判"国际版没有每日签到"——错在把"我们请求里没有"当成了"服务端
> 没有"。用户看到客户端弹窗显示"每天领 100 Credits"可领,推翻了该结论,
> 才有了下面的决定性实验。教训见文末。

## 背景

用户 Windows 上 Qoder CN 版可正常签到,mac 上装的 Qoder 国际版"无法签到"。
第一版取证(当日早些时候):链路全通——解锁登录态、令牌、API 域推导、campaigns
请求全部正常,唯一活动 `act-20261020-944`(首购双倍积分)已领,`claimable=false`,
遂误判"没有每日签到"。当晚用户出示客户端弹窗截图(每天领 100 Credits + 领取
按钮 + 每日 10:00 刷新倒计时),重启取证。

## 决定性实验:风控头对照

同一 token、同一端点 `GET /sash/api/v1/me/campaigns`,只变请求头:

| 请求头组合 | 返回活动 |
|---|---|
| 现有头(仅 `Cosy-ClientType: 10`) | 只有 `act-20261020-944` |
| + Accept-Language / Origin / Electron UA / x-locale | 不变 |
| 换 `Cosy-ClientType` 值(1~20/Web/Desktop 扫描) | 不变(8/10 返回同 1 个,其余空) |
| + `Cosy-MachineId`(数据目录 `auth.machine-id`) | 不变 |
| **+ `Cosy-MachineToken/Code/Type`(mac 版 `umid/runtime-info` 输出)及 OS/Hostname/Version** | **多出 `act-20261102-287`(每日 100 Credits,`CLAIMABLE`)** |

领取实验:`POST .../campaigns/{id}/claim` → `{"status":"CLAIMED","replayed":false,
"benefit":{"kind":"CREDITS","amount":100,...}}`;随后 `GET /sash/api/v2/me/usage`
的 `addOnQuota.total` 200 → 300,到账确认。

## 每日活动的真实形态(实测)

- 活动族 `act-YYYYMMDD-NNN`(如 `act-20261102-287`),**campaign key 固定、
  每日滑动窗口**(10:00 ~ 次日 09:59,UTC+8),每天 `CLAIM_BENEFIT` 100 Credits,
  领取后 30 天有效,全模型可用。
- 客户端弹窗就是 `campaignUrl`(`growth-page/activity-iframe`)webview,asar 里
  `openCampaignSurface` 给它注入 `apiBaseUrl/clientType/getAccessToken`——与脚本
  同一端点同一鉴权,**唯一差别就是设备风控头**。
- 服务器语义:风控头不齐全的请求**不下报错**,只是静默少给每日活动——与
  NOTE-02 的"缺 `Cosy-ClientType` 返回空列表"同一设计思路,极易误判。

## mac 上的设备标识来源(与 Windows 对照)

| 头 | Windows | mac(实测) |
|---|---|---|
| `Cosy-MachineToken/Code/Type` | `resources/umid/runtime-info.exe --account-stdin` | `Contents/Resources/umid/runtime-info --account-stdin`,输出同为 JSON |
| `Cosy-MachineId` | 数据目录 `auth.machine-id` | 同(本次实测 `com.qoder.app.stable/auth.machine-id`) |
| `Cosy-MachineOS` | `%s_windows` | `arm64_darwin`(服务端不校验取值格式,实测可领) |
| `Cosy-MachineHostname` | `hostname()` | 同 |
| `Cosy-Version` | `build-manifest.json` 的 `productVersion` | 同(mac 实测 0.4.9) |

## 修复方向(as-built 待做)

1. `domains/qoder/risk.py` 去 Windows 专用化:umid 组件按平台找 mac 路径
   (`/Applications/Qoder.app/Contents/Resources/umid/runtime-info`),`Cosy-MachineOS`
   出 `_darwin` 后缀。
2. `domains/qoder/api.py` 的 `build_api` 去掉 `IS_WINDOWS and sess` 门禁,mac 也组风控头。
3. "没有可领取活动"的结果码从 `FAILED` 拆出 `NO_CAMPAIGN`(needs_attention=0),
   避免台账整排 FAILED 误导。

## CN/国际版差异清单(修订)

| 维度 | CN 版 | 国际版 |
|---|---|---|
| 数据目录 | `%APPDATA%\com.qodercn.app*` | `~/Library/Application Support/com.qoder.app.stable` |
| API 域 | `openapi.qoder.com.cn` | `openapi.qoder.sh` |
| 每日签到 | 有(每天 10:00 刷新窗口) | **有,同构**(此前误判已修正) |
| 每日活动下发条件 | 需 umid 设备头(NOTE-02 A3) | **同样需要**(mac 版 runtime-info 同样可用) |
| 活动页 JS | v0.0.730 | v0.0.748,埋点同构 |

## 教训(取证方法)

1. **"我的请求看不到"≠"服务端没有"**。服务端静默过滤(不给报错、少给数据)时,
   结论必须排除完请求形态差异(头/设备标识/客户端版本)才能下。
2. **用户看到的 UI 是最强反证**。客户端弹窗显示可领,就一定有一条我们没复刻的
   请求形态;从 UI 文案("每日 10:00 刷新")反查比扫 asar 更快。
3. 台账的历史成功记录(`act-20261102-503`、每日 +100)是"活动存在"的既有证据,
   第一版取证时没回查,教训:下"不存在"结论前先查自家台账。

## 凭据与安全(不变)

- mac 解锁链:Keychain `Qoder App Safe Storage` → PBKDF2 → AES-128-CBC,直接复用
  仓库 `auth.py`;客户端运行中改写 `auth.v1.dat` 有 `_read_store_retry` 兜底。
- 本笔记不记录 token/machineToken 等敏感值;设备标识可通过组件重新生成读取。
