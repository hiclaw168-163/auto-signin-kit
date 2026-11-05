# STD-07: 声明式应用接入(web_signin 引擎)

状态: 生效(2026-11-05) | 适用: 新 Web 签到应用接入的默认路径

## 定位

新应用接入的**默认路径是写一份 YAML,不是建一个域**。域(`STD-01` 三件套)
留给编排型/多流程应用;「status 查询 → 幂等 claim → 确认」同构形态的普通
签到应用一律声明式:

- 应用声明: `config/apps/<app_code>.yaml`,一份 YAML 一个应用,零 Python、
  零 entry_points 改动(重跑 `schedule install` 即入调度);
- 引擎: `domains/web_signin/`(它本身仍是一个标准域,受全部架构门禁约束);
- 台账对齐: `app_code` 同时是 `ask_signin_runs.domain` 列与
  `ask_apps.app_code`,引擎落库直接用它,无转换层。

命名空间: 声明式 `app_code` 与 entry_points 域名共用一个命名空间,
scaffolding 新建域时两边都查重(`scaffolding.service.validate`)。

## YAML 字段字典

| 字段 | 必填 | 说明 |
|---|---|---|
| `app_code` | ✓ | `^[a-z][a-z0-9_]{1,31}$`,**必须与文件名一致** |
| `region` | | `cn` / `intl`(默认 `cn`),落 ask_apps 主档 |
| `display` | ✓ | 展示名 |
| `base_url` | ✓ | `https?://…` |
| `auth.kind` | ✓ | 适配器: `plain`(JSON 令牌文件)/ `ss_aes`(Trae 信封) |
| `auth.files` | | 凭据候选: plain=文件 glob;ss_aes=数据目录(空=平台默认) |
| `auth.env` | | 环境变量令牌(**仅 default 账户消费**,具名账户隔离) |
| `auth.token_json_path` | plain✓ | 令牌在文件 JSON 里的点路径 |
| `auth.expires_json_path` | | 有效期字段点路径 |
| `auth.token_header` | | `{name, prefix}` → `{name}: {prefix}{token}` |
| `auth.headers` | | 适配器产出之上的静态追加头 |
| `headers` | | 全局静态头(所有请求;优先级低于凭据头) |
| `business_codes` | | 业务码→结果标签,只允许 `AUTH_REJECTED/FORBIDDEN/ERROR` |
| `code_path` / `code_ok` | | 业务码字段点路径(默认 `code`)/ 成功值(默认 0) |
| `status.method/path/body/mapping` | | 查询声明;`mapping` 统一字段→点路径,键限 `checked_in/credits/extra_credits` |
| `claim.checked_in_path` | | 响应回显"已签"的判定点路径(有则免复查) |
| `claim.amount_path` | | 领取数额点路径(缺省回落 status 映射的 credits) |
| `claim.retry_on` | | 限流业务码 → 同轮退避重试 30+60+120s(预算内) |
| `claim.confirm_by_status` | | 受理成功但响应不回显时,以 status 复查三轮确认 |
| `schedule.times/limit_minutes` | | 调度默认;可被 schedule.yaml tasks 覆盖 |
| `accounts` | | 多账户(`core` AccountRef: name/auth_file/auth_dir) |

校验: `extra="forbid"`——拼错字段当场报(`schema.AppDeclaration`)。
没有 `status` 的纯 claim 型应用合法,但**必须**显式 `claim.amount_path`,
引擎不猜数额来源。

## 引擎行为(解释器契约)

- 流程 = trae 域 `claim_once` 的成熟设计(NOTE-03): status 先行,未签才
  claim;claim 写操作默认不重试;`retry_on` 命中才退避重试;预算耗尽带
  限流结果收尾交下一轮调度;
- 结果集/退出码与全库口径一致: `CLAIMED/ALREADY/DRY_RUN` → 0,其余 → 1;
- 凭据失败三分类: `NO_AUTH`(码 2)/`AUTH_ERROR`(码 1)/`AUTH_REJECTED`(码 1),
  引导语在适配器里给。

## 适配器(authkit)

登录态形态真实地有两种以上(AGENTS 规则 5),故设适配器注册表
(`authkit.ADAPTERS`,kind → 模块): 契约 = `resolve/doctor/ready` 三函数。
现有: `plain`(JSON 令牌文件)、`ss_aes`(Trae SS_AES 信封,密码学实现随
引擎走)。**OS 差异只进 core/adapters**(`STD-05`),适配器不写平台分支。

## 调度与应用同步

- `web_signin/schedule_meta.py` 扫描 `config/apps/*.yaml` 生成任务
  (import 期,坏文件 stderr 警告不炸;真实运行时严格报错);
- 任务名: `WebSignin<TitleCase>`(launchd label 安全),账户实例沿用
  全库口径 `@账户` + `+i 分钟`错峰;
- 新增/修改声明后重跑 `auto-signin-kit schedule install`;
- 应用同步(`POST /api/apps/sync`): 声明式应用就绪 = `authkit.ready`
  (不解锁凭据),同步建 ask_apps 主档 + default 户。

## 命令面

```
auto-signin-kit web_signin run    --app <code> [--dry-run] [--account NAME]
auto-signin-kit web_signin status --app <code>
auto-signin-kit web_signin doctor --app <code>
auto-signin-kit web_signin list
```

## 新增一个声明式应用的清单

1. 取证: 端点/请求头/响应形状(写 `docs/notes/NOTE-XX-*.md`,见 `STD-06`);
2. 写 `config/apps/<app_code>.yaml`(从本规范的字段字典出发);
3. `auto-signin-kit web_signin doctor --app <code>` → 登录态可读;
4. `auto-signin-kit web_signin run --app <code> --dry-run` → 流程符合预期;
5. `auto-signin-kit web_signin run --app <code>` 真实领取一次,台账/日志核对;
6. `auto-signin-kit schedule install`(入调度);
7. 测试: `tests/domains/web_signin/` 的桩式用例已覆盖引擎流程,应用侧
   只需按需补端点形状回归(有非平凡解析时);
8. 门禁三件套。
