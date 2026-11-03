# SPEC-11: 内核抽取与应用管理(三阶段)

> 状态: 已实现(阶段一 K1-K6 六提交 / 阶段二脚手架 / 阶段三应用管理;2026-11-03)。
> 实施修正: 重建进程必须跑在项目 .venv(``uv run``)而非 tool venv——调用方
> 自己就锁死 ``uv\tools\...\\Scripts``,沿用 sys.executable 必然 os error 5。
> 背景: 目标是支撑未来数十至上百个签到域的规模化接入。结论建立在四域(实际五域,
> `qoder` 与 `trae` 高度同构)的共性实证分析上(2026-11-03)。
> 相关: STD-01(新增域规范)、SPEC-02(core 公共契约)、SPEC-09(Web 管理台)。

## 需求与约束

- 后续要批量接入大量签到平台,需要"应用"可管理: 创建、修改(配置/调度)、删除、查看。
- 签到动作按复杂度分两类:**网页版签**(HTTP/URL 类)与**客户端签到**(本地客户端/CDP 类)。
- 实证结论(四域共性分析): 各平台接口/交互差异极大,**通用低代码引擎不可行**,
  签到逻辑必须写代码;但域之间的**胶水层**存在大量真实同构,可抽取。

## 总路线: 内核抽取 → 脚手架 → UI 应用管理

顺序不可颠倒: 脚手架模板是内核的消费者,先抽内核,模板才能保持极薄;
否则模板里复制 300 行胶水,每修一个内核 bug 所有已生成域都要重新生成。

---

## 阶段一: 内核抽取(纯重构,现有 5 域行为不变)

新增子包 `core/kit/`(避免 core 根目录膨胀)。6 个独立小提交,风险从低到高,
每个提交迁移对应域并跑门禁(`ruff check && lint-imports && pytest -q`):

| 提交 | 模块 | 内容 | 迁移对象 |
|---|---|---|---|
| 1 | `core/kit/tokens.py` | `_parse_iso` + `token_expired`(参数化过期字段名) | trae、qoder(逐字重复) |
| 2 | `core/kit/budget.py` | `parse_budget(default, cap)`: env 优先/非法回落/越界夹上限 | workbuddy、trae、qoder |
| 3 | `core/kit/transport.py` | 传输层错误→结果标签映射(NO_NETWORK→NETWORK、BUDGET_OUT→TIMEOUT、401/403→…) | trae、qoder(顺带消掉 qoder 重复两次的 if 链)、doubao |
| 4 | `core/config/schema.py` | 增量加 `EndpointConfig`/`BudgetConfig` mixin(`base_url`、`budget_seconds`) | trae、doubao、qoder |
| 5 | `core/kit/actions.py` | `run_action` 骨架: 未知命令分支、`done_today` 静默跳过、emit 包装 | 全部 5 域 |
| 6 | `core/kit/cli.py` | `create_domain_app(...)` 工厂: typer app + auto/status/doctor 命令组 + 异常→退出码映射 | trae、doubao、qoder 整文件替换;workbuddy 保留自定义命令只复用骨架;schedule 语义不同不强迁 |

次优先项(锁文件重试读取、`SENSITIVE` 脱敏登记)跟随所在提交顺手做,不单独开提交。

### 明确不进内核的(防泄漏抽象)

- **`is_installed`**: 三处实现本质不同(注册表/目录存在性/glob),平台知识留在域内
  与 `core/adapters/client_discovery.py`。
- **凭证解密链路**: helper 子进程 / SS_AES / DPAPI / Chromium v10+CDP,只有调用骨架像。
- **签到业务流与 RetryPolicy 参数**: 各域实测/风控取证结论,永远留在域内。

## 阶段二: 脚手架(开发流程自动化)

`core/scaffolding/` 服务层(纯业务,不 import typer),两个入口共用:
CLI `auto-signin-kit scaffold new` 与 UI API。

- **生成物**(全部引用阶段一内核,目标 ~200 行/域):
  `domains/<name>/{__init__,schema,service,cli,schedule_meta}.py` +
  `config/domains/<name>.yaml` + `config/examples/<name>.yaml.example` +
  `tests/domains/<name>/test_contract.py` + pyproject 两行 entry-points。
- **两种模板**: `web`(HTTP 签到: base_url + 凭证配置 + 签到接口桩)、
  `client`(客户端签到: CDP 探测 + 客户端识别桩,复用豆包域已验证模式)。
  `schedule_meta.py` 声明 `KIND = "web" | "client"` 供 UI 展示与调度探测分类。
- **校验**: 合法 Python 标识符、不与现有域/关键字冲突、目标目录可写。
- **生效编排**(处理 Windows tool 重装与运行中 serve 互斥的 os error 5 坑):
  serve 收到请求 → spawn **独立重建进程** → serve 自己优雅退出 →
  重建进程执行 `uv sync` + `uv tool install --editable '.[web]' --force` →
  `ui serve --daemon` 拉起新实例。
  **任一步失败回滚全部生成文件,不留半成品域**。
- 生成后状态: 域已注册但签到逻辑是桩,契约测试红;开发者填实现至测试绿。

## 阶段三: UI 应用管理

前端新增"应用管理"视图;后端复用现有注册表发现,**不引入新状态源**。

- **列表页**: 名称、类型(web/client 徽标)、启用状态、最近结果、下次调度时间——
  数据来自 `_known_domains()` + 台账 + schedule,零新表。
- **新建向导**: 名称、类型、目标站点、每日时间 → 触发脚手架 →
  轮询"生成代码→重装生效"进度 → 完成后跳详情(提示"逻辑是桩,测试红,去填实现")。
- **详情页**: 配置编辑(复用 config_api)、调度管理(复用 schedule UI)、
  doctor、最近事件(复用台账)。
- **启停**: 调度层面禁用(复用现有 `TaskOverride` 机制),不删代码。
- **删除**: 两档——停用归档(默认)与物理删除(输入域名二次确认 →
  删目录 + 摘 entry-points + 走阶段二同款重建编排 + 回滚)。

---

## 测试策略

- 阶段一: 现有 331 测试护航 + 每个内核模块新增单测;架构门禁
  (`test_domain_cli_files_do_not_import_business_modules` 等)确保 kit 不越界。
- 阶段二: 生成物契约测试(生成 → 门禁全绿 → 桩测试红 → 回滚后无残留)。
- 阶段三: API 层 pytest;前端按 AGENTS 纪律,涉及渲染/视觉的改动必须人工验收。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 阶段一大范围重构改坏行为 | 6 个小提交逐域迁移,每步门禁全绿;行为等价由现有测试断言 |
| Windows 重装编排失败留下半成品 | 独立重建进程 + 失败回滚生成文件;编排自身可单测(桩化) |
| 脚手架模板腐化(内核变了模板没跟) | 生成物契约测试里断言模板 import 的内核符号存在 |
| UI 新建与 CLI 手工建域并存混乱 | 文档口径统一: UI 新建 = 触发同一 scaffolding service,产出与手工 STD-01 流程完全一致 |
