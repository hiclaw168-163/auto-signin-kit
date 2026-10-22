# SPEC-05-trae域

> **概要**: Trae CN 桌面端每日签到自动领取的 as-built 实现。
> **状态**: 已实现(本机真实验证过: doctor 解密、status 全通、auto 幂等 ALREADY;
> CLAIMED 分支本机今日已由 IDE 签到占位,待未签日首跑验证)
> **关联**: [SPEC-01](SPEC-01-架构总览.md) · [SPEC-02](SPEC-02-core层公共契约.md) ·
> 逆向母本 [NOTE-03-trae签到接口逆向](../../notes/NOTE-03-trae签到接口逆向.md)

## 1. 模块分工(6 文件)

| 文件 | 职责 |
|------|------|
| `cli.py` | 命令入口(auto/status/doctor) |
| `service.py` | run_action: build_api → check_status → (未签时)claim → emit |
| `api.py` | `Api`(status/claim)+ 预算解析 + `log_path` + SENSITIVE |
| `auth.py` | storage.json 发现 + SS_AES 信封解锁 + 令牌有效期 |
| `doctor.py` | 离线体检: 解密/有效期/账号 |
| `schema.py` | TraeDomainConfig(base_url/log_empty/budget_seconds/data_dir) |

## 2. 凭据解锁链(auth.py)——SS_AES,全平台可用

- 位置: `<数据目录>/User/globalStorage/storage.json` 的
  `iCubeAuthInfo://icube.cloudide`(CN 端 authProviderId;缺省回退任意
  `iCubeAuthInfo://` 键)。
- 数据目录发现: 显式覆盖(配置 `data_dir`,透传 `load_session(override_dir)`)
  > `TRAE_AUTH_DIR` > 平台默认(Windows `%APPDATA%\{Trae CN,Trae}`;
  macOS `~/Library/Application Support/…`;Linux `~/.config/…`)。
- 信封(base64): 头 `[116,99,5,16,0,0]` + 内嵌随机主密钥 32B + AES-128-CBC 体;
  key/iv = `sha512( sha512(master) + 盐表[Gle^zle] ).digest() + 64×0` 的前 32B;
  明文 = 前 64B `sha512(JSON)` 校验和 + JSON。
- AES 操作走 `core/adapters/win_dpapi` 的 `decrypt_cbc`(cryptography 优先/CNG 兜底)。
- **易混淆坑**: `~/.trae-cn/trae-jwt-token` 是**沙箱临时令牌**(Byte-Cloud 工具用),
  拿它调 ug 接口回业务码 1001——真登录态只在 storage.json。
- 不碰 refreshToken(约 6 个月有效);`expiredAt`(约 20 天)过期即上报重登。

## 3. 接口契约(逆向)

宿主 `https://api.trae.cn`(TRAE_BASE_URL 覆盖):

| 端点 | 动作 | 说明 |
|------|------|------|
| `/trae/api/v2/ug/checkin_credits/status` | POST | body `{"req_source": 1}`(对齐 NOTE-03 2026-10-21 复核) |
| `/trae/api/v2/ug/checkin_credits/claim` | POST | body 同上;写操作默认不重试,**唯一例外 9074 限流**(见 §4) |

- 鉴权 `Authorization: Cloud-IDE-JWT <用户JWT>`(JWT RS256,`iss=trae`)。
- `x-device-id` 取 storage.json 明文键 `telemetry.devDeviceId`(配 `device_meta`
  的 3×2s 锁重试): **2026-10-21 复核 claim 已强制设备头,缺失回业务码 9004**;
  status 不校验。
- 业务码走 HTTP 200 + `code` 字段: `0`=成功,**`1001`=无法认证→AUTH_REJECTED**
  (先于通用 HTTP 判定);`9074`=活动期限流。
- 正常响应: `checked_in / credits / extra_credits / enable / did_checked_in`。

## 4. 业务映射(service.py)

先 status 后 claim(IDE 的同款节奏):

| 状况 | result | 退出码 |
|------|--------|--------|
| 未签 → claim 成功 | `CLAIMED`(credits/extra_credits) | 0 |
| 已签 | `ALREADY` | 0 |
| `--dry-run` 且未签 | `DRY_RUN` | 0 |
| 业务码 1001 / HTTP 401 | `AUTH_REJECTED` | 1 |
| HTTP 403 | `FORBIDDEN` | 1 |
| 业务码 9074(活动限流) | `RATE_LIMITED`(见下) | 1 |
| 其他 code≠0 | `ERROR`(带 code+message) | 1 |

**9074 限流重试**(2026-10-22 加入): 1024 活动期服务端会以"参与人数过多"限流
claim。claim 服务端幂等,按"确认幂等的签到接口可重试"的项目约定在**同轮内**退避
20s/40s 重试(共 3 次尝试,预算不足时提前收尾),全失败才回 `RATE_LIMITED` 交给
下一次调度——否则一天三次触发全撞限流就整日白跑。写操作其余场景仍不重试。

结果每轮必写(ALREADY/INACTIVE 也落盘,记录即定时任务心跳);日志文件
`trae.log`(`TRAE_SIGNIN_LOG` 覆盖)。

## 5. 配置与环境变量

`TRAE_BUDGET_SECONDS` 优先 → 非法回落/越界夹 **240**;`TRAE_TOKEN`(CI/测试)、
`TRAE_AUTH_DIR`、`TRAE_BASE_URL`、`TRAE_SIGNIN_LOG`。

## 6. doctor 输出要点

`token_source`(env/desktop)、`storage`(凭据文件路径)、`token_expires_at`、
`user_id`、`username`、`decrypted`、过期与否。

## 7. 与 IDE 自动签到的关系

IDE 启动即自动调 status→claim(可从 `logs/<ts>/main.log` 的 ttnet 记录确认),
与本域是**幂等双保险**: 谁先调谁真实领取,后到者拿 ALREADY。

## 8. 失效模式

- 桌面端升级换信封头/存储位置 → auth.py 的魔数断言先红;逆向母本留了取证路径。
- 新增活动(checkin_lottery 等)走同一 `claim` 族端点,接端点请扩 `api.py` 并同步本 SPEC。
- 多账号切换: userId 变化由 storage.json 现值决定,不做任何缓存。
