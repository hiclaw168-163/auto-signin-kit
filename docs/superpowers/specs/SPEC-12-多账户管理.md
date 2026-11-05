# SPEC-12 多账户管理

> 状态: **已实现**(2026-11-05 三阶段全落地,门禁三绿 450 passed;
> 真机验证: 四域 doctor/auto 走 default 路径回归正常,`--account ghost`
> 显式报错,`/api/accounts` 返回真实账户,未登记账户 404,`schedule render`
> 单账户输出与从前逐字节一致)。调度按账户展开(SPEC-12 §6 第 3 条)也已落地:
> `schedule_meta.accounts()` 声明具名账户 → `select_tasks` 展开成
> `<任务名>@<账户>`(触发时间按账户序号 +i 分钟错峰),配置缺失降级单账户
> (`ConfigMissingError`),格式/校验错当场炸。
> 同日附带两项台账体验修补: ①重复失败降噪(emit_result 落库前查
> `failure_count_today`,硬失败当天第 2 次起不刷 needs_attention,契约见
> SPEC-02);②workbuddy 成长中心积分并入台账 credits 快照(此前
> credits_gained 只进 report 文本,管理台今日积分少算旅行礼物/任务奖)。
> P2 实现: AccountRef 增四个凭据位置字段(auth_file/auth_dir/user_data_dir/
> cdp_port,语义由各域解释),kit 钩子 build/doctor 增加 `account` 形参;
> 具名账户只认显式路径并**隔离全局环境变量令牌**(QODER_TOKEN/TRAE_TOKEN/
> WORKBUDDY_AUTH_FILE 是 default 户的,混用会拿 A 户令牌签 B 户的到)。
> P3 实现: /api/accounts(登记 × 逐账户统计合并)、/api/accounts/enabled
> (只改台账登记态,调度启停真相在 YAML 配置,面板如实提示)、
> /api/run 带 account(不同账户可并发,同账户同动作互斥)、前端"账户管理"页。
> P1 与设计的偏差: ①`accounts` 字段放 DomainConfig 基类;②停用账户调度
> 跳过不落台账(与 skip_done_today 先例一致,结果词表无 SKIP)。
>
> 背景: 项目由单应用脚本演化而来,接入应用变多后扩展变慢;且每个应用
> (域)实际存在多个账户(如 workbuddy 大号/小号、qoder 主力/备用),
> 当前凭据加载"取第一个可用的就用",无法管理。

## 1. 目标与非目标

### 目标

- **一域多账户**: 每个域可声明 N 个命名账户,各自独立凭据、独立台账、独立调度。
- **账户粒度全程透传**: CLI `--account` → 动作执行 → 台账落库 → 聚合统计,
  全链以 `(domain, account)` 为业务主体维度。
- **向后兼容**: 不声明 accounts 的配置等价于单账户 `default`,现有配置零改动可跑。
- **Web 管理台可管账户**: 账户列表/状态/手动触发单账户签到(阶段三)。

### 非目标

- 不做跨域账户联邦(一个全局账户映射多域凭据)——域之间保持独立是硬规则。
- 不做凭据池轮转选号(workbuddy2api-panel 的核心是 API 网关的选号负载均衡,
  我们是签到工具,一个账户该签到就签到,没有"选号"问题)。
- 不做凭据加密存储(维持现状口径: 本地文件 + 台账脱敏)。

## 2. workbuddy2api-panel 调研结论与借鉴取舍

调研对象: `https://github.com/linguo2625469/workbuddy2api-panel`
(4.7k stars,Go 实现,把 WorkBuddy 账号包装成 OpenAI 兼容 API 的多账号
网关 + Web 面板;2026-10-21 已停止维护,但多账户管理的设计仍值得看)。

### 2.1 它怎么管理多账户

| 机制 | 实现 |
|------|------|
| 账户注册表 | **目录即注册表**: `auths/workbuddy-<uid>.json` 一账户一文件,池启动时扫描目录自动对齐,加号=丢文件 |
| 凭据与状态分离 | 凭据在 `auths/*.json`(明文 token + uid/nickname 元信息,0600,原子写 tmp+rename);运行状态(积分/冷却/计数/paused)在 `data/state.json` |
| 账户生命周期 | 面板添加(OAuth 授权落盘,热加载免重启)/ 暂停(临时退出选号,保号任务照跑)/ 禁用(终态,默认连任务也跳)/ 移除 |
| 配置分层 | 全局 `config.json`(池策略/调度/冷却)+ 环境变量覆盖 + 每账户仅凭据文件,无数据库 |
| 面板 | 账号池视图(每账户积分/状态/冷却)/ 用量 / 配置热更新(深合并+原子替换) |

### 2.2 借鉴什么、不借什么

**借鉴(采纳)**:

1. **一账户一份凭据文件,按账户名命名** —— 各域凭据 profile 化的落盘形态。
   加账户=加一份文件+一行配置;删账户=删文件。目录结构天然承载"多账户",
   不需要为凭据引入第二存储。
2. **凭据与运行状态分离** —— 凭据是文件(域自治),账户登记与运行记录是
   SQLite 台账(`ask_accounts` / `ask_signin_runs`)。我们**已天然如此**,
   本设计延续,绝不在凭据文件里复制台账字段。
3. **paused/enabled 两态语义** —— `ask_accounts.enabled` 列已预留,接通:
   `enabled=0` 的账户调度跳过(连任务也不跑,比 panel 的"暂停"更省),
   但 CLI 显式 `--account` 指定仍可手动跑(手动操作永远放行)。
4. **面板的账户视图形态** —— 账户列表(名称/最近成功/积分/needs_attention)
   + 单账户手动触发按钮,P3 直接照此形态做。

**不借鉴(及理由)**:

1. **目录扫描发现制** —— panel 靠扫目录发现账户;我们的原则是"配置显式
   声明,非法即报错"(`extra="forbid"`)。折中: **YAML 声明账户名单,
   凭据文件按名字解析**,文件缺失在 build 时显式报错,绝不静默扫目录凑数。
   台账侧保留"首次成功运行自动 upsert `ask_accounts`"(发现制只用于记账,
   不用于执行)。
2. **选号池/加权轮转/熔断冷却** —— 网关负载均衡的东西,签到场景无此问题。
3. **无数据库** —— panel 不用 DB 是单二进制部署取舍;我们已有 SQLite 台账,
   且 `ask_accounts` 维表建好了,不推倒。

## 3. 总体设计

### 3.1 账户模型

```
配置(YAML, 显式声明)                凭据(文件, 按账户名落盘)          台账(SQLite)
┌─────────────────────┐    解析    ┌──────────────────────────┐    upsert   ┌──────────────┐
│ domain: qoder       │ ────────→ │ credentials/<account>/…  │ ──────────→ │ ask_accounts │
│ accounts:           │  account  │ (每域方言不同,见 §5)      │  运行时发现  │ ask_signin_  │
│   - name: main      │           └──────────────────────────┘             │   runs.account│
│   - name: backup    │                                                    └──────────────┘
│     enabled: false  │
└─────────────────────┘
```

- **账户名约束**: `[a-z0-9][a-z0-9_-]{0,31}`,全库唯一键是
  `(domain, name)`(与 `ask_accounts.UNIQUE` 对齐)。
- **`default` 是保留名**: 单账户配置等价于声明 `accounts: [{name: default}]`,
  凭据路径维持现状(不动存量用户)。
- **enabled**: schema 字段,默认 `true`;`false` 时静默调度跳过(落一条
  SKIP 台账),显式 `--account` 手动跑放行。

### 3.2 透传链(阶段一打通)

```
cli.py (--account, 缺省 "default")
  → service.run_action(action, config, account=…)
      → skip_done_today(domain, action, account=…)      # 按 (domain,account) 查
      → self.build(config, account=…)                    # 阶段一先忽略,阶段二接
      → 钩子返回 DTO
      → _emit(out): out["account"] = account             # emit_result 无需改签名
  → ingest.record_payload: record["account"] 已支持      # ingest.py:70 现成
```

关键事实(盘点确认): `emit_result` 的 `out` dict 整行进事件,
`ingest._insert_signin_run` 已经 `record.get("account") or "default"`,
`ENVELOPE_FIELDS` 已含 `account`(进信封列不进 response)。**透传链的
终点是现成的,只需域侧开始往 out 里放 account**。

## 4. 阶段一(P1): 数据层与透传

改动面: core + 各域 schema/cli,不动凭据加载(凭据仍是"第一个可用的")。

1. **schema**: `DomainConfig` 增加可选嵌套模型
   ```python
   class AccountRef(DomainConfig):
       name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
       enabled: bool = True
   ```
   `BudgetedConfig`/各域 schema 增加 `accounts: list[AccountRef] = []`
   (空 = 单账户 default)。账户名重复/含 `default` 冲突时校验报错。
2. **CLI**: 各域 cli 增 `--account`(默认 `"default"`),传给 run_action;
   `--account` 不在声明名单时显式报错(不静默兜底)。
3. **core.kit.domain**: `run_action(..., account="default")`;
   `_emit` 里 `out.setdefault("account", account)`。
   workbuddy 手写 run_action 同步加参数与透传。
4. **core.kit.actions**: `skip_done_today(domain, action, *, account)`;
   `runs.done_today(domain, account)` —— 查询加 `AND account = ?`。
5. **runs.py 聚合改双维**:
   - `compact_history`: GROUP BY `date(executed_at), domain, account`
     (否则第二账户的成功行会被当冗余删掉——多账户场景的真数据丢失点);
   - `overview_stats`: 每域改每 `(domain, account)` 一份;
   - `ask_daily_checkins` 视图加 account 维(迁移重建视图)。
6. **history 域**: 查询输出加 account 列/过滤参数。
7. **ask_accounts 接通 enabled**: 调度路径跳过 `enabled=0` 的账户;
   ingest upsert 时保留既有 enabled 不覆盖。

验收: 四域现有测试全绿 + 新增多账户台账隔离测试(两账户同天各一条成功行,
compact_history 后都保留;done_today 互不误判)。

## 5. 阶段二(P2): 四域凭据 profile 化

统一约定: 各域凭据解析入口增加 `account` 参数;账户名 → 凭据路径的映射
由各域方言决定,但**命名规则全库统一为 `credentials/<domain>/<account>/`**
(或域现有目录 + `<account>` 子目录),`default` 账户沿用现状路径不动。

| 域 | 现状(单账户语义) | profile 化方案 | 难度 |
|----|------------------|---------------|------|
| workbuddy | `credentials.py:48` `find_auth_file()` 取第一个存在的 auth 文件 | auth 文件按 `<account>.json` 并列存放;`build(config, account)` 按名解析,缺失显式报错 | 低 |
| qoder | `auth.py:79` `find_auth_dirs()` 多目录但 `load_session()` 取第一个可读即返回 | 目录→账户映射: `find_auth_dirs()` 返回有序候选改为按 account 过滤;`account=default` 时维持"第一个可读"现状 | 中 |
| doubao | `cookies.py:177` 多 Chromium profile 但同一登录用户,单 `user_data_dir` 根 | 每账户独立 `user_data_dir`(配置里 per-account 覆盖 `user_data_dir`/profile 目录);CDP 兜底 target 过滤按账户目录 | 中 |
| trae | `auth.py:31` 凭据与设备强绑同一文件(`iCubeAuthInfo://icube-dc:<数字>` 键名藏设备号) | **每账户一个设备数据目录**(独立安装目录或 user-data 拷贝),凭据键名里的设备号随目录走;schema per-account 声明 `data_dir` | 高 |

trae 特别说明: 凭据=设备同文件意味着"多账户"实际是"多设备目录",
P2 最后做;若用户实际只有一个 trae 账户,允许 trae 的 accounts 声明
存在但凭据仍共用(P2 内该域可后置,不阻塞整体)。

doctor: 各域体检输出按账户分节(体检哪个账户的凭据写清楚)。

## 6. 阶段三(P3): Web 管理台账户视图

形态借鉴 panel 的账号池视图(SPEC-09 增量,不新建 SPEC):

1. **账户列表页**: 按 (domain, account) 展示——最近成功时间/14 天色块/
   今日积分/needs_attention/`ask_accounts.enabled` 开关。
   数据源: `overview_stats()` 双维化后的现成结构。
2. **单账户手动触发**: 账户行内"立即签到"按钮 → 后台起对应域 CLI
   `--account <name>`(复用 SPEC-10 的后台运行机制),结果落台账。
3. **账户增改**: 本期只做 `enabled` 开关与备注类档案字段(daily_potential 等
   已有列);凭据文件的创建仍走各域 CLI(P2 的 doctor/登录指引),
   面板不直接代写凭据文件——凭据是敏感面,UI 不碰。

## 7. 兼容与迁移

- 无 schema 迁移: `ask_signin_runs.account` / `ask_accounts` 早已存在。
- 视图 `ask_daily_checkins` 加 account 维需要 DROP+CREATE(迁移脚本已有
  该模式,老库幂等)。
- 存量数据: 老行 account 已回填 `'default'`(store_migrations._MIGRATIONS),
  双维聚合对老数据天然正确。
- 现有配置(无 accounts 字段): 全链等价单账户 `default`,行为逐字节不变;
  凭据路径不搬家。

## 8. 测试计划

- **架构门禁**: 现有三件套规则不动;新增约定——账户名归一/校验逻辑放
  core(`core.kit.accounts` 或 schema 内),不在各域 cli 重复实现。
- P1: 台账双维隔离(done_today/compact/credits 视图)、`--account` 透传
  (emit 的 out 含 account)、未声明账户显式报错、enabled=0 调度跳过。
- P2: 每域凭据按账户解析(default 路径不变更的回归测试必须每域一条)。
- P3: API 层账户列表/开关/手动触发的接口测试(沿用 SPEC-09 测试口径)。

## 9. 实施顺序与门禁

P1 → P2(workbuddy → qoder → doubao → trae)→ P3,每阶段独立 PR,
每阶段过 `uv run ruff check && uv run lint-imports && uv run pytest -q`,
并按 AGENTS.md 规则 6 手动跑一次真实命令。本 SPEC 实现完成后改状态行,
并同步 SPEC-01 数据流小节(账户维)与 SPEC-02(透传契约)。
