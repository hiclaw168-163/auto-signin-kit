# Trae CN 签到接口逆向笔记(2026-10-21)

> 逆向取证 · 对应 as-built: [SPEC-05-trae域](../superpowers/specs/SPEC-05-trae域.md)

结论先行:**Trae CN 存在每日签到系统,已完整打通并并入 `trae` 域**。
协议简单:两条 POST + 一个本地加密信封。

## 证据链(六步)

1. **workbench 宿主侧**: `%LOCALAPPDATA%/…/resources/app/out/main.js` 内嵌
   `CheckinLottery` 服务(`fetchCheckin/action/queryInfo`),活动名 `checkin_1024`。
2. **端点获取**: webview HTTP 缓存(`%APPDATA%\Trae CN\Cache\Cache_Data\f_00000a`)
   里的 icube SDK JS 暴露完整端点族 `/trae/api/v2/ug/checkin_credits/*`。
3. **宿主定位**: `product.json` → `bootConfig.ug.trae.normal = https://api.trae.cn`。
4. **鉴权头**: SDK `Authorization: Cloud-IDE-JWT ${token}`;主进程
   `_applyUgDeviceHeaders` 还附 x-device-id 等五件套——实测**非必需**(服务端不校验)。
5. **凭据定位**: 主进程 `UserStorage.persistedKey = "iCubeAuthInfo://icube.cloudide"`,
   落在 `%APPDATA%\Trae CN\User\globalStorage\storage.json`。
   坑:".trae-cn/trae-jwt-token" 是**沙箱临时令牌**(Byte-Cloud 工具用),
   拿它调 ug 接口会回业务码 1001——真登录态在 storage.json。
6. **实测**: storage.json 解密出的 JWT 直接通过 status/claim(设备头全省略也 200)。

## 协议速查

- `POST https://api.trae.cn/trae/api/v2/ug/checkin_credits/status`
  body: `{"Request": {}}`(Content-Type: application/json)
- `POST https://api.trae.cn/trae/api/v2/ug/checkin_credits/claim`
  body 同上;服务端幂等,本域主张写操作不重试。
- 响应: `{"code":0,"checked_in":true,"credits":100,"extra_credits":100,
  "enable":true,"did_checked_in":false,"message":"success"}`
  — `code` 是业务码(1001=无法认证,即 HTTP 200 里的 auth 拒绝)。
- 用户 token 形状: JWT(RS256, `iss=trae`), payload.data = {user_id, tenant_id, type}。
  有效期约 20 天;refreshToken 约 6 个月。脚本不碰 refresh,过期请重登桌面端。

## 凭据信封(byteCrypto SS_AES,无 DPAPI,跨平台)

`storage.json → iCubeAuthInfo://icube.cloudide` = base64:

```
[116,99,5,16,0,0] + randKey[32] + AES-128-CBC(key,iv,ct)
key/iv 派生: sha512( sha512(randKey + salt[Gle^zle]) )[0:32]
盐: Gle^zle;  明文 = sha512(body)作为前 64 字节校验 + body(JSON)
```

实现: `src/auto_signin_kit/domains/trae/auth.py`;AES-CBC 在
`core/adapters/win_dpapi.py(cryptography 优先 / CNG 兜底)`。

## IDE 自动签到行为

IDE 启动 08:38 自动调用 status→claim(无需用户点),签到弹窗活动也走同一条
`claim` 接口。所以"自研脚本"与 IDE 内置自动签到是**幂等双保险**:
谁先调,谁就会真实领取(checked_in=true, credits+100/天,活动期 extra_credits 另加)。
