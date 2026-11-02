# SPEC-09: Web 管理台(ui 层 + frontend 前端包)

状态:**规划中(设计定稿,未实现)**。实现后本行改为"已实现"并补齐偏差。

## 1. 定位与边界

本地 Web 管理台,给"配置一次后不想碰命令行"的场景一个可视化入口:

- **看板**: 各域今日/近期签到状态、连签、积分,一眼可见
- **手动触发**: 一键跑某个域的 auto / doctor
- **调度管理**: 查看/注册/注销系统定时任务(调 schedule 域现成逻辑)
- **历史台账**: 签到记录明细与聚合(数据源 = history 域,零新建表)

明确**不做**的(v1 范围收敛,防 UI 变成双倍维护面):

- 不做配置表单编辑——配置的事实源仍是 `config/domains/*.yaml`,git 留痕;
  UI 只读展示。表单编辑后期再加(校验用各域 pydantic schema 现成)
- 不做远程访问/多用户/认证——只绑 `127.0.0.1`,这是本机工具不是服务
- 不在 UI 里重复实现任何业务逻辑

## 2. 已定架构决策(与 ADR 呼应)

| 决策 | 结论 | 理由 |
|------|------|------|
| UI 形态 | FastAPI(后端 API)+ Vue 3(前端 SPA) | 生态成熟;浏览器访问 localhost |
| 前端位置 | **`frontend/` 与 `src/` 同层** | Node 工具链不进 Python 包;语言边界 = 最强隔离 |
| Python 侧位置 | `src/auto_signin_kit/ui/`,与 domains 平级 | API 层要进程内调 schedule/history 的 service,必须留在包内 |
| 依赖安装 | `[web]` extra(fastapi/uvicorn) | CLI/schtasks 无头安装保持精瘦,装 UI 才引 web 栈 |
| 触发对接 | 子进程调 `auto-signin-kit` CLI | 复用退出码与 JSON 日志契约;ui→domains 不 import 业务 |
| 数据对接 | 进程内调 history/schedule service | 纯查询,读现成 SQLite 台账,无子进程开销 |
| 未来拆包 | 不预付;目录已隔离,需要时半天拆出 | ADR-0002 同款判据:第二种情况真出现才付代价 |

## 3. 目录结构(实现后)

```
auto-signin-kit/
├── pyproject.toml               # [project.optional-dependencies] web = fastapi+uvicorn
├── src/auto_signin_kit/
│   ├── cli.py                   #   `ui serve` 子命令(缺 fastapi 显式报错,退出码 2)
│   └── ui/                      # ✦ 新增,只有 API 层,无页面
│       ├── app.py               #     FastAPI 实例 + StaticFiles 托管 frontend/dist
│       ├── api.py               #     路由: overview/history/events/schedule/jobs/doctor
│       ├── runner.py            #     子进程运行器(线程池 + job 登记,见 §5)
│       └── schema.py            #     响应模型(pydantic)
├── frontend/                    # ✦ 新增,纯 Node 包(Vue 3 + Vite + TS + Naive UI)
│   ├── package.json  vite.config.ts   # dev: proxy /api -> 127.0.0.1:8000
│   └── src/
│       ├── views/               #     Dashboard / History / Schedule / Doctor
│       └── api/                 #     薄封装 fetch,类型对齐 ui/schema.py
└── (dist/ 由 npm run build 产出,.gitignore 排除)
```

import-linter 分层契约加一层(最顶上): `ui` → `cli` → `domains` → `orchestration` → `core`,
并新增禁令 `ui 不得 import 各域 cli`(业务只经 service 或子进程)。frontend 在
Python 包之外,天然零 import,不需要也不可能有 Python 侧契约。

## 4. API 面(全部 `/api` 前缀,JSON)

| 方法/路径 | 作用 | 数据来源 |
|-----------|------|----------|
| `GET /api/overview` | 各域今日状态 + 近 7 天摘要 | `history.service.daily_status(1)` + 台账聚合 |
| `GET /api/history?days=&domain=` | 每日折叠视图 | `history.service.daily_status` |
| `GET /api/events?date=&domain=&attention_only=&limit=` | 明细记录 | `history.service.events` |
| `GET /api/schedule` | 定时任务注册状态 | `schedule.service.status`(进程内) |
| `POST /api/schedule/install` / `uninstall` | 注册/注销,body 可选域名数组 | `schedule.service.install/uninstall`(进程内,原样透传失败项) |
| `GET /api/doctor/{domain}` | 诊断报告 | 子进程 `auto-signin-kit <域> doctor`,取 JSON 输出 |
| `POST /api/run/{domain}` | 手动触发 auto | 子进程 + job 登记(§5),立即返回 job id |
| `GET /api/jobs/{id}` | 轮询 job 状态/输出 | runner 内存登记 |

约定:

- 业务失败(HTTP 200 + 业务错误体)与基础设施错误(HTTP 4xx/5xx)分开,
  对齐 CLI"业务错退出码 1 / 环境错 2"的口径
- 写操作(install/uninstall/run)一律走 POST,GET 只读——前端可以放心预渲染
- 服务默认绑 `127.0.0.1:8688`(端口可配),启动日志打印访问地址

## 5. 子进程运行器(runner.py)

- 线程池(上限 2)跑 `subprocess.run`,job 登记 `{id, domain, argv, started_at,
  status: running|ok|failed, exit_code, output}`(内存 dict,进程退出即弃——
  正式结果反正由 service 的 emit_result 落台账,runner 只管"这次手动点的人
  等到什么")
- 前端轮询 `GET /api/jobs/{id}`(1s),completed 即渲染结果卡片
- 每域同时只允许一个 auto job(幂等接口重复触发无害,但排队日志会乱)

## 6. 前端页面(frontend/)

Vue 3 `<script setup>` + TypeScript + Naive UI,四个视图:

1. **Dashboard(默认)**: 每域状态卡(今日结果/积分/连签,数据来自 overview;
   running 状态轮询刷新),失败卡 `needs_attention` 高亮
2. **History**: `ask_daily_checkins` 折叠视图表格 + 域/天数筛选;点行展开
   `events` 明细
3. **Schedule**: 任务列表(名称/触发时间/是否注册),install/uninstall 按钮
   带确认,失败项红条透传
4. **Doctor**: 选域跑诊断,渲染 JSON 报告(cookie/CDP/客户端检测这些文字
   报告的结构化展示)

## 7. 前置小改: doubao 进台账

`history.service.SIGNIN_DOMAINS` 目前是 `("workbuddy", "qoder", "trae")`,
doubao 的 emit_result 是否已落 `ask_signin_runs` 需核实;若未落,实现 UI 前
先补(doubao auto 的 result/report 字段口径与其他域一致),否则看板缺豆包卡。

## 8. 测试与门禁

- Python: `tests/ui/` 用 FastAPI TestClient;runner 的子进程全桩(单元测试
  绝不真跑签到);schedule 路由桩 service,history 路由读 tmp_path 台账
- 前端: v1 只做 `npm run build` 通过 + 手动冒烟(渲染类功能测试绿不代表
  看起来对,见 AGENTS.md 门禁一节的教训);组件测试后期视复杂度补 vitest
- 门禁命令不变: `uv run ruff check && uv run lint-imports && uv run pytest -q`;
  CI 有 Node 环境时加 `npm run build` 一步,没有就本地保证
- `ui/` 下文件同样受 410 行上限约束,路由多了就按资源拆文件

## 9. 实现顺序

1. 前置: doubao 进台账核实/补齐(§7)
2. `pyproject.toml` 加 `[web]` extra + importlinter 分层加 ui
3. `ui/`(app/api/runner/schema)+ `cli.py` 的 `ui serve` + `tests/ui/`
4. `frontend/` 脚手架(Vite+Vue3+TS+Naive UI)→ 四视图 → build 接 StaticFiles
5. 真机验收: `auto-signin-kit ui` 打开看板,手动触发一次 doubao auto,
   schedule 开关走一遍;门禁全绿
6. 本 SPEC 改"已实现";GUIDE-01 补 UI 安装/启动一节
