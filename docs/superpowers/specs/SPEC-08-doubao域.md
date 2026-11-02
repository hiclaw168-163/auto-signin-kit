# SPEC-08: doubao 域(豆包签到领额度)

> 接口取证与加密细节见 `docs/notes/NOTE-04`;本文只记设计决策与命令面。

## 1. 数据流

```
cookies.py                    api.py                    service.py
豆包本地 Cookie 解密    →  DoubaoApi(checkin/detail)  →  run_action(claim/status/doctor)
(多 profile 合并 +            (AGW 公共查询参数)           (emit_result → 台账)
 CDP 兜底)
```

## 2. 关键决策

- **从客户端本地解 Cookie 而非让用户贴 token**: 豆包无独立 token 配置,
  登录态只在客户端 Chromium 库里;DPAPI + v10 解密全自动,零人工输入。
- **多 profile 合并(库 mtime 新者优先)**: 活动面板 webview 用独立
  `Profile N` 存储,持有最新会话;`Default` 可能是过期的。
- **CDP 兜底**: `Profile N` 运行期被独占锁读不了;客户端带
  `--remote-debugging-port` 启动时走 `Network.getCookies` 拿实时值,
  优先级最高(文件库可能持有已过期的旧会话)。
- **不做随机伪造**: 解不出必需 cookie 时如实报错并给可行动指引
  (关闭客户端落盘 / 带端口启动),不用随机值硬闯。
- **幂等重试**: 服务端幂等(`idempotent:true`),网络类失败可安全重试。

## 3. 命令面

```bash
auto-signin-kit doubao auto              # 签到(幂等;--dry-run 只探落地页/--once 不重试/--silent)
auto-signin-kit doubao status            # 活动是否在期(只读)
auto-signin-kit doubao doctor            # 离线体检: 数据目录/Local State/Cookie 解密/CDP 可达性
```

## 4. 失败模式(§对应 service 分支)

| 现象 | result | 处置 |
|------|--------|------|
| Cookie 缺失/库被锁且 CDP 不可达 | NO_AUTH(exit 2) | 关闭豆包客户端落盘后重试;或带调试端口启动 |
| `code=710012001` 登录已过期 | ERROR | 本地会话旧于服务端: 重登客户端/走 CDP |
| `code=2` campaign not found | INACTIVE_ERROR | 活动换季: 更新 config 的 campaign_code |
| `code=710010702` system error | ERROR | 参数/活动态问题,查 NOTE-04 是否接口变更 |

## 5. 测试隔离

与 history 域同坑: emit 落库走根 `tests/conftest.py` 的全局隔离;
本域另需把 `cookies_via_cdp` monkeypatch 掉(测试机上若客户端恰带调试端口
运行,真 CDP 会污染断言)。
