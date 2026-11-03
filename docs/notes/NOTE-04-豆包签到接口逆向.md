# NOTE-04: 豆包(doubao.com)签到接口逆向

> 2026-11-02 取证 + 全链路实测收官。活动: "签到领额度"(2026-10-30 上线),
> 每日 200 额度、周一翻倍、累计 30 天最高 8800;漏领不补、赠送额度 30 天有效。

## 1. 客户端与登录态

- 豆包电脑版(Electron/Chromium 147 壳),数据目录
  `%LOCALAPPDATA%/Doubao/User Data`;活动面板以 webview 加载
  `https://www.doubao.com/pc/agent-explore`(入口: 左下角弹窗或头像侧边栏
  「免费用豆包」;**仅个人账号**,飞书企业账号被排除)。
- 登录态是 Chromium Cookie(`sessionid` 等),**分库存放**:
  - `Default/Network/Cookies` —— 登录页/主窗口,运行时可共享读绕锁;
  - `Profile N/Network/Cookies` —— 活动面板 webview,**持有最新会话**,
    被 webview 独占打开(WinError 32),客户端运行期间读不了。
- 加密: 标准 Chromium v10(`v10` 前缀 + 12B nonce + AES-256-GCM),
  密钥在 `Local State` 的 `os_crypt.encrypted_key`(DPAPI);
- **应用层值前缀**: 解密明文前 32 字节是客户端自加的常量前缀,剥离后才是
  真实值(实测 sessionid 为 32 位十六进制串)。

## 2. 接口契约(2026-11-02 实测)

- 公共查询参数**缺一不可**(缺了报 invalid param/system error):
  `version_code=20800&language=zh&device_platform=web&doubao_device_platform=web
  &aid=497858&real_aid=497858&pkg_type=release_version`
- 签到(幂等):
  `POST /alice/ug/campaign/landing/checkin/` body `{"scene": "pc_incentive_task_mvp_v3"}`
  → `{"code":0,"data":{"checked_in":true,"checkin_date":"2026-11-02",
  "continuous_days":1,"idempotent":true,"reward_amount":400,
  "reward_type":"quota_grant"}}`;重复调用回 `idempotent:true`。
- 活动详情: `POST /alice/ug/campaign/v3/detail/`
  body `{"campaign_group_code": "pc_incentive_task_mvp_v3"}`;
  code=2 "campaign not found" = 活动换季(改 config 的 campaign_code)。
- 鉴权完全靠 Cookie;无 a_bogus/msToken 签名要求。业务错误码:
  710012001=登录已过期(cookie 旧),710010702=system error(参数/活动态)。

- 领任务奖励(2026-11-02 实测):
  `POST /alice/ug/campaign/item/reward/claim/` body
  `{"task_id": "", "campaign_group_code": "...", "campaign_item_code": "<campaign_code>",
  "AgwCommonParam": {}}`(task_id 对活动项为空串即可);
  不可领时 HTTP 仍 200,回 `data.biz_error.code=3`(toast 提示);
  可领取时无 biz_error,奖励直接入账。
- 任务/里程碑状态机: `task_status` 1=未完成(须先在产品里真实完成对应操作)、
  2=已完成待领取、3=已领取;里程碑 reward_mode=2 为达标自动发放。
  任务/里程碑清单在 `v3/detail` 的 `tasks[]/milestones[]`(campaign_code 是主键)。

## 3. 本工具的实现与已知边界

- `domains/doubao/cookies.py`: 多 profile 合并(库 mtime 新者优先)+ 32 字节
  前缀剥离 + CDP 兜底(`cdp.py`,客户端带 `--remote-debugging-port` 运行时
  取实时 Cookie,优先级最高);缺 cookie 时报错给可行动指引。
- **已知边界**: `Profile N` 运行期读不了,而 `Default` 可能持有已被服务端
  判过期的旧会话(实测: 更新客户端重登后 Default 仍旧)——所以客户端带调试
  端口运行时最稳;或关闭客户端让 Cookie 落盘后再跑。CDP 端口默认 9222,
  `DOUBAO_CDP_PORT` 可覆盖。
- 换季预案: 服务端换 scene 时 `doubao status` 会报 campaign not found,
  更新 `config/domains/doubao.yaml` 的 `campaign_code` 即可(前端 chunk 里
  的 `4264.*.js` 是接口定义所在,新 scene 从活动面板 webview 请求里抓)。

## 4. macOS 排查结论(2026-11-02 实测)

- 数据目录布局与 Windows 不同: 目录就是
  `~/Library/Application Support/Doubao` 本身(无 "User Data" 层);
  Cookie 库在 `Default/Cookies` / `Profile N/Cookies`(**无 `Network/` 子层**;
  实测 "Profile 1/Cookies" 的 sqlite URI 直连会失败,先拷贝再开)。
- Keychain 密钥可读: `security find-generic-password -s "Doubao Browser Safe Storage" -w`
  (acct "Doubao Browser",base64 解码后 16B);另有 `Doubao Safe Storage`
  (acct "Doubao")。framework 二进制(147.0.7727.149)含 `saltysalt`/
  `Safe Storage` 字符串,确为 Chromium os_crypt 魔改。
- **但文件解密未破解,如实报 UNSUPPORTED**: cookie blob 为 `v10` 前缀 +
  密文,三条 cookie **首块密文完全相同**(存在固定 16B+ 应用层头);黑盒
  穷尽约 300 组合(AES-128/256-CBC/GCM × PBKDF2 盐/迭代 × 直接哈希)无一命中
  ——不做假支持,mac 链路只走 CDP。
- **CDP 探测防误报**: 9222 端口可能被其他 Chromium 应用占用(实测 Qoder IDE),
  `cdp.probe()` 会校验 `/json/version` 的 `Browser` 字段包含 "doubao"。

### 4.1 macOS CDP 使用指引

1. **完全退出豆包客户端**(否则调试端口起不来/被正常实例占用);
2. 带调试端口启动内置 helper:

   ```bash
   DOUBAO_CDP_PORT=9223 "/Applications/Doubao.app/Contents/Helpers/Doubao Browser.app/Contents/MacOS/Doubao Browser" --remote-debugging-port=9223
   ```

3. 另开终端跑 `auto-signin-kit doubao auto`(CDP 实时 Cookie 优先级最高,
   doctor 的 `cdp_available` 应为 true);
4. 用完退出 helper,正常启动豆包即可。

### 4.2 2026-11-03 排查实录: 签到 710010702 + CDP 兜底两处失效

现象: 调度 00:05/09:05 两次 `710010702 系统错误`(前一晚 23:05 还正常)。

**会话层(已修复)**:
- 客户端长期开着,会话在服务端过期后只存在于内存,Cookie 库不落盘
  (落盘时间停在会话过期前),调度读到过期会话;
- CDP 兜底当时也失效,两处:
  1. Chromium 111+ 拒绝带 `Origin` 头的 CDP WebSocket 握手(403),
     `websocket-client` 默认发 Origin——握手必须 `suppress_origin=True`;
  2. `probe()` 曾按 `/json/version` 的 Browser 字段含 "doubao" 校验身份,
     但新版豆包(Chrome/147 内核)只报 `Chrome/147...`——真豆包被拒。
     身份安全改由 target URL 过滤保证(异应用占口时拿不到 doubao cookie);
- 修复后 CDP 取到内存实时会话(detail/status 全链路通),`710012001`
  登录过期映射为 NO_AUTH(台账词表有标签,report 给重开客户端指引)。

**服务层(非我方问题,2026-11-03 当日实测)**:
- 会话有效(过了登录关)、活动在期、`checked_in_today=false` 的前提下,
  checkin 接口仍回 `710010702`;
- **决定性实验**: 在豆包页面上下文内用浏览器原生 fetch(同源+真实 cookie+真实头)
  发同样请求,服务端照样报错——非请求构造/签名/风控参数问题;
- 官方客户端界面点「签到」同样 toast「签到失败，请稍后重试」;
- 前端请求体实为 `{scene, biz_params: undefined}`(序列化省略),与我们一致;
  先调 `landing/report` 上报事件也不影响结果。结论: 服务端故障,等恢复,
  调度高密度触发 + 接口幂等会自动补签。

**教训**: doctor 的 `session_ok` 只验 cookie 格式(本地能力),验不了服务端
登录态;"系统错误"类网关码会掩盖真实死因,网关码→result 的映射要前置到
api 层,不能靠事后猜。
