# SPEC-04-qoder域

> **概要**: Qoder CN 桌面端每日 Credits 自动领取的 as-built 实现。
> **状态**: 已实现(本机真实验证过: 解密登录态、真实领取 100 Credits、重复运行幂等 ALREADY)
> **关联**: [SPEC-01](SPEC-01-架构总览.md) · [SPEC-02](SPEC-02-core层公共契约.md) ·
> 逆向母本 [NOTE-02-qoder积分面分析](../../notes/NOTE-02-qoder积分面分析.md)

## 1. 模块分工(7 文件)

| 文件 | 职责 |
|------|------|
| `cli.py` | 命令入口(auto/status/doctor) |
| `service.py` | run_action: build_api → 分类领取 → emit;`QUIET_RESULTS=("ALREADY","INACTIVE")` |
| `api.py` | `Api`(campaigns/claim)+ 预算解析 + `log_path` + SENSITIVE 登记 |
| `auth.py` | 数据目录发现 + `auth.v1.dat` 解锁 + 令牌有效期 |
| `risk.py` | 设备风控头(机器 token;`resources/umid` 未逆向,缺失不影响领取) |
| `doctor.py` | 离线体检: 解密/有效期/设备组件 |
| `schema.py` | QoderDomainConfig(base_url/log_empty/budget_seconds) |

## 2. 凭据解锁链(auth.py)

- 目录: `%APPDATA%\com.qodercn.app*` glob,`QODER_AUTH_DIR` 覆盖,按 mtime 排序。
- 文件 `auth.v1.dat`: 前 3 字节 `v10` 魔数 → 12B nonce → GCM 密文 + 16B tag。
- 密钥: 同目录 `Local State` 的 `os_crypt.encrypted_key`(DPAPI 包裹,
  去 `DPAPI` 前缀后 `unprotect`)→ `decrypt_gcm`(cryptography 优先/CNG 兜底)。
- 读到半截(客户端正在重写)短重试 3 次(0.25s 间隔)。
- **不碰 refreshToken**: 不与桌面端争抢刷新轮换;`token_expired(expiresAt)` 判过期,
  过期即 `AUTH_REJECTED` 请用户重登桌面端。

## 3. 接口契约(逆向)

宿主 `https://openapi.qoder.com.cn`(`QODER_BASE_URL` 覆盖):

| 端点 | 动作 | 说明 |
|------|------|------|
| `/sash/api/v1/me/campaigns` | GET | **缺 `Cosy-ClientType: 10` 头服务端返回空列表**(api.BASE_HEADERS) |
| `/sash/api/v1/me/campaigns/{id}/claim` | POST | **空体** `b""`;默认不重试防重复 |

查询可重试(网络/5xx/429 各自退避);claim 与 401/403 不重试。

## 4. 业务映射(service.py)

分类: `benefit(actionType==CLAIM_BENEFIT)→ active(窗口内)→ todo(CLAIMABLE)/claimed`。

| 状况 | result | 退出码 |
|------|--------|--------|
| 可领取且领取成功 | `CLAIMED`(credits 求和;领取后含 token_expires_at) | 0 |
| 窗内已领取 | `ALREADY` | 0 |
| 活动在但不 CLAIMABLE | `PENDING` | 1 |
| 无活动/窗口外 | `INACTIVE` | 1 |
| `--dry-run` | `DRY_RUN` | 0 |
| HTTP 401 / 403 | `AUTH_REJECTED / FORBIDDEN` | 1 |

领取过程任一 401/403 直接终止(换凭据才有意义,继续领后面的也没用)。

## 5. 配置与环境变量

`QODER_BUDGET_SECONDS` 优先 → 非法回落默认/越界夹 **240**(PT5M 任务上限 = 300-60 余量);
`QODER_TOKEN` / `QODER_AUTH_DIR` / `QODER_BASE_URL`;日志 `qoder.log`(`QODER_SIGNIN_LOG` 覆盖);
空跑强制写日志 `QODER_SIGNIN_LOG_EMPTY=1`。

## 6. doctor 输出要点

`token_source`(env/desktop)、`auth_dir`、`token_expires_at`、`device_identity`
(设备风控头是否拿到;false 时提示"不影响领取")、过期与否。

## 7. 失效模式

- 桌面端换凭据格式(v10→v11?) → auth.py 魔数检查会先红。
- 积分面新玩法: 逆向结论是"未来会出现在 `openapi.qoder.com.cn/growth-page/*` 新 iframe",
  当前无宠物类第二积分入口;收获端点时更新本 SPEC 与分析母本。
- `Cosy-ClientType` 头变化 → 空列表即症状,先查这里。
