# PROPOSAL-01 多端应用架构调研与方案(CLI/Web/桌面/小程序/App)

> 状态: 提案待评审 · 2026-11-06 · 调研人: AI(基于公开资料 + 本仓库现状)
> 目标: 给出**可操作、可拆解、可演示、可复用**的多端应用参考架构,
> 未来新业务/新系统可直接套用。

---

## 一、调研结论摘要(公开资料)

### 1.1 主流多端架构模式

| 模式 | 核心思想 | 适用 | 来源 |
|---|---|---|---|
| **API-First + 单一契约源** | 先定 OpenAPI/契约,再按契约生成各端 client;契约是唯一真相 | 所有端共用一套稳定业务 API | Swagger/OpenAPI 实践、百度智能云《API与BFF协同》 |
| **BFF(Backend for Frontend)** | 按前端形态(web/移动/小程序)各设一个薄聚合层,做字段裁剪/聚合/协议适配;按"体验类型"而不是"设备"分 BFF;无状态 | 端之间体验差异大、字段需求差异大 | uplatz/codeCake《BFF Pattern》、devtechinsights《BFF 2025》 |
| **六边形架构(Ports & Adapters)** | 领域核不依赖任何交付机制;CLI/HTTP/UI 都是 **驱动适配器(driving)**,DB/凭据/外部服务都是 **被驱端口(driven)** | 一核多壳的天然答案——这正是"一套内核,五端复用"的结构 | asadqi《Hexagonal Should Be Default》、Chakray 实施指南 |
| **Monorepo 共享包** | pnpm workspaces 起步,构建缓存需要时上 Turborepo/Nx;`packages/api-client`、`packages/ui`、`packages/types` 跨端共享 | 多端代码同仓演进,原子化改动 | freetoolkit《Monorepo Tools 2025》、字节《大前端 Monorepo 最佳实践》 |

**共识结论**: 多端架构 = **一个领域核 + 一份契约 + N 个薄壳**。
BFF 不是必选项——端之间的差异如果只是"渲染形态不同、字段相同",
一份 OpenAPI 契约 + 类型生成即可;差异真的大了(如小程序不适合流式、
App 需要推送)才引入按端 BFF。

### 1.2 各端技术选型(2025 现状)

| 端 | 候选 | 2025 结论 | 对本项目的适配判断 |
|---|---|---|---|
| CLI | Python typer / Rust clap | typer 已在用,子命令+工厂注册成熟 | ✅ 已有(`core/kit/cli.py` 工厂) |
| Web | Vue3/React SPA | Vue3 + naive-ui 已在用 | ✅ 已有(`frontend/`) |
| 桌面 | **Tauri** vs Electron | Tauri: <3MB、Rust 安全、可复用 Web 前端;Electron: 生态最成熟但体积/内存大。结论: **Tauri 壳 + 现有 Vue 管理台 = 桌面端几乎零增量** | ⭐ 最低成本路径 |
| 小程序 | **Taro**(React 系) vs **uni-app**(Vue 系) | 2025 两者都是编译时多端方案;uni-app 生态最全(微信/支付宝/抖音/H5/App 全覆盖),Vue3 语法与现有前端技能栈一致 | ⭐ 一套代码编译微信小程序 + App |
| App(原生级) | uni-app(App 端) vs React Native vs Flutter | 性能差距已缩到感知不到;uni-app 一套代码同时产出小程序+App,H5/管理台还能复用组件 | ⭐ 与小程序同栈,避免双栈维护 |

**选型总原则(可复用到任何新业务)**:
1. **桌面端永远用壳复用 Web 端**(Tauri/Electron),不为桌面单独开发;
2. **小程序 + App 优先同栈编译方案**(uni-app),不为两端分别维护;
3. **所有端只消费契约**,不允许端直接 import 业务实现。

---

## 二、现状盘点(哪些已经有了,哪些缺)

本仓库离目标架构**比想象近得多**——六边形结构的骨架已经存在:

```
已有(✅ 直接复用)                      缺口(❌ 本提案补)
─────────────────────────────         ─────────────────────────────
✅ 领域核: core/kit(SigninDomain       ❌ API 无契约化: ui/api.py 手写
   基类/actions/budget/transport),        FastAPI 路由,没有 OpenAPI 契约
   core/declarative(声明式应用+           管理与 TS 类型自动生成
   认证适配器),core/ports+adapters    ❌ 桌面端: 无壳(Web 管理台只跑
✅ 驱动适配器: typer CLI(entry_            在浏览器,调度日志要看命令行)
   points 域注册 + cli.py 三件套)      ❌ 小程序/App: 无任何移动端形态
✅ 被驱端口: httpclient/budget/        ❌ 多业务复用: "域注册+声明式应用"
   scheduler/win_dpapi/mac_keychain       模式已可复用但没有抽成独立模板
✅ 状态层: SQLite 台账(单文件、         ❌ 端间实时性: 全部轮询/定时,
   WAL、幂等迁移、审计字段)               无事件推送(SSE/WS),暂不需要
✅ Web 管理台: Vue3+naive-ui+路由      ❌ 鉴权: 本机 127.0.0.1 无鉴权,
   分组/主题/积分统计                     出本机部署必须补
✅ 调度: schtasks/launchd 端口适配
✅ 测试与架构门禁: import-linter
   分层契约 + 门禁测试(强制的!)
```

**判断**: 不需要推倒重来。目标架构 = 把现有六边形骨架**补上契约层**,
再**长出三个壳**(桌面/小程序/App),最后**把内核抽成可复用模板**。

---

## 三、目标架构

### 3.1 分层总图

```
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────────┐ ┌─────────┐
│ CLI 壳  │ │ Web 壳  │ │ 桌面壳  │ │ 小程序/App 壳 │ │ 未来新端 │
│ typer   │ │ Vue3    │ │ Tauri   │ │ uni-app      │ │ ……     │
│ (已有)  │ │ (已有)  │ │ (复用Web)│ │ (编译双端)    │ │         │
└────┬────┘ └────┬────┘ └────┬────┘ └──────┬───────┘ └────┬────┘
     │ 本地直调   │ HTTP      │ HTTP(IPC)   │ HTTPS        │
┌────▼───────────▼───────────▼─────────────▼──────────────▼────┐
│              L2 契约层: OpenAPI 3.1 契约(单一真相源)           │
│   FastAPI 自动产出 openapi.json → openapi-typescript 生成      │
│   packages/api-client(TS 类型+请求封装,五端共用)              │
│   (差异大的端才加 BFF 薄层: 现阶段不需要)                       │
├──────────────────────────────────────────────────────────────┤
│              L1 应用服务层: FastAPI + 用例编排                  │
│   /api/overview /api/credits/stats /api/apps/sync …           │
│   鉴权(本机 token → 部署后 JWT/设备码) · SSE 事件流(预留)      │
├──────────────────────────────────────────────────────────────┤
│              L0 领域核(已有,不动)                              │
│   core/kit 引擎 · core/declarative 声明式底座 · store 台账     │
│   core/ports(Portocol) + core/adapters(schtasks/launchd/      │
│   win_dpapi/mac_keychain/httpclient)                           │
└──────────────────────────────────────────────────────────────┘
```

**依赖方向铁律**(进架构门禁): 壳 → 契约 → 应用服务 → 领域核;
领域核不知道任何壳的存在。

### 3.2 五端形态定义

| 端 | 形态 | 与内核的关系 | 首个可演示物 |
|---|---|---|---|
| CLI | 本机进程,直调领域核(不经 HTTP) | 同进程 import | 现状即演示 |
| Web | 浏览器 SPA ↔ FastAPI | HTTP + JSON 契约 | 现状即演示 |
| 桌面 | **Tauri 壳包 Web 管理台**,内置 sidecar 跑 FastAPI(或直连本机服务) | 同 CLI 同机 | 一个 <10MB 的 .exe/.app,双击出管理台 |
| 小程序 | uni-app 编译微信小程序,调云端 API | HTTPS + 契约,只读+触发类操作 | 真机扫码看积分统计页 |
| App | uni-app 同一套代码编译 Android/iOS 包 | HTTPS + 契约 + 推送(预留) | 与小程序同一演示 |

**关键取舍**:
- CLI 保持直调领域核(本机工具,经 HTTP 是脱裤子放屁);其余端全部走契约。
  CLI 与 API 的行为一致性靠"同一应用服务函数被两者调用"保证,不靠网络。
- 小程序/App 是**云端形态**: 服务核必须可部署(现在 FastAPI 只绑 127.0.0.1,
  拆解阶段加部署形态 + 鉴权),台账从 SQLite 换 Postgres 由被驱端口吸收
  (store 接口不变——这正是六边形的价值)。
- 不做 GraphQL/ProtoBuf: 单一 OpenAPI + TS 生成已够,协议升级留给真实痛点。

---

## 四、可拆解路线图(每步有验收物)

### P0 契约层(地基,~2 天)
- [ ] `ui/api.py` 路由补 response_model/pydantic schema,产出规范 OpenAPI 3.1
- [ ] 新增 `frontend/packages/api-client`:`openapi-typescript` 生成类型 +
      薄封装(现 `frontend/src/api/index.ts` 演进)
- [ ] 契约冻结进门禁: 生成产物不入库,CI 校验"生成物与提交一致"
- **演示**: 改一个接口字段 → `npm run gen:api` → 前端类型报错 → 全链路可见
- **验收**: 门禁三绿 + Web 全功能回归

### P1 桌面端(最便宜的新端,~2 天)
- [ ] `desktop/` 加 Tauri 工程: devUrl 指 Web 管理台构建产物;sidecar 方式
      拉起 FastAPI(或要求用户先装 CLI 服务,壳只做窗口)
- [ ] 系统托盘: 下次签到时间/今日状态一屏(数据来自同一契约)
- **演示**: 双击桌面图标 → 原生窗口出管理台 → 托盘显签到状态 → 体积对比 Electron
- **验收**: Win/mac 双平台打包成功,功能与 Web 管理台等价

### P2 小程序 + App(云端形态,~4-6 天)
- [ ] 服务核部署形态: Dockerfile + 反代 + JWT 鉴权(设备码绑定) + Postgres 端口适配器
- [ ] `mobile/` uni-app 工程(Vue3): 首页(总览卡)+积分统计+历史,复用
      api-client(请求层换 uni.request 适配器——契约不变)
- [ ] 微信小程序先发(审核/发布流程走通),App 端出 Android APK
- **演示**: 微信开发者工具真机预览 → 扫码看积分趋势 → 云端触发一次签到 → 推送回执
- **验收**: 小程序过审上架(或企业内测版),APK 可安装自更新

### P3 可复用性沉淀(把这套东西变成模板,~3 天)
- [ ] 抽 `templates/multi-platform-service/`: 领域核脚手架 + 契约层 + Tauri 壳
      + uni-app 空壳,新业务 `copr init` 即得五端骨架
- [ ] 沉淀 STD-08《多端架构规范》: 分层铁律/契约流程/端准入标准
      (什么业务配什么端,不为五端而五端)
- [ ] 架构门禁扩展: "壳不得 import 领域核"(现有 import-linter 加合同)
- **演示**: 用模板 30 分钟搭一个"健康打卡"演示系统(CLI+Web+桌面三端)
- **验收**: 模板生成的新项目过全部门禁

### 明确不做(防过度设计)
- ❌ GraphQL/gRPC/ProtoBuf——OpenAPI 够用
- ❌ 微前端/多 BFF——单业务单核,端差异由契约版本吸收
- ❌ WebSocket 推送——调度类业务轮询/定时已够,事件流留 SSE 接口位
- ❌ Electron、Flutter、RN——避免三套客户端栈并存

---

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| 服务核出本机部署后,凭据(客户端本地登录态)不在云端 | 凭据留在各机器 CLI 侧,云端只收结果上报;**凭据永远不出本机**是安全红线,进 STD-08 |
| uni-app 编译层黑盒(踩坑成本) | P2 先做只读页面验证链路,写操作最后接;小程序不做的能力不硬做 |
| Tauri sidecar 在 Windows 的杀软误报 | 先出"仅窗口壳"版本(依赖本机已装 CLI),sidecar 打包作为 P1.5 |
| 双机开发(Gitee/GitHub 平行) | 拆解任务按 P0→P3 顺序单线程推进,避免同文件并行冲突 |

---

## 六、一句话总结

> **不换骨架,补契约,长三壳,沉模板。**
> 领域核(六边形)已经存在且被门禁保护;第一步不是写新端,而是把
> API 变成契约;有了契约,桌面端是壳、小程序/App 是编译产物、
> 未来任何新业务拿到的是一份可复制的多端模板。
