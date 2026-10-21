# auto-signin-kit

各编程助手(WorkBuddy / CodeBuddy 系、Qoder CN、Trae CN)每日签到、领奖的
自动化工具箱。用 `polydomain-cli` 的多域 CLI 架构搭出来的项目: 新增一个域
不用碰组合根,新增一种外部系统支持不用改其他域。

---

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
uv tool install --editable . --force
```

装完之后 `auto-signin-kit` 就是一个全局命令。`--editable` 意味着改代码不用重装,
但如果改了 `pyproject.toml`(尤其是新增域),要重新跑一次
`uv sync && uv tool install --editable . --force`——域注册表写在包的
dist-info 里,只改文件不会自动生效。

---

## 用法

```bash
auto-signin-kit --help                     # 列出所有域
auto-signin-kit workbuddy --help           # 看某个域有哪些命令
auto-signin-kit workbuddy auto             # WorkBuddy 签到 + 成长中心(幂等)
auto-signin-kit workbuddy doctor           # 离线检查凭据与解密运行时
auto-signin-kit qoder auto                 # Qoder CN 每日 Credits 领取
auto-signin-kit qoder auto --dry-run       # 只报告可领取项,不真领取
auto-signin-kit trae auto                  # Trae CN 每日签到领取(幂等)
auto-signin-kit trae doctor                # 离线检查登录态可读性与有效期
```

每个命令输出**一行 JSON**(stdout 由机器读,过程日志走 stderr),字段
``result`` / ``report`` / ``needs_attention``;退出码非 0 就是有事要人处理。

静默(`--silent`)模式的结果写进**平台日志目录**(不落在仓库/cwd 根目录):
Windows 为 `%LOCALAPPDATA%\auto-signin-kit\logs\signin.log`(qoder 为
`qoder.log`,trae 为 `trae.log`),`WORKBUDDY_SIGNIN_LOG` /
`QODER_SIGNIN_LOG` / `TRAE_SIGNIN_LOG` 可覆盖;编码固定 UTF-8。

退出码约定:

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 业务失败(网络/认证/领取失败等) |
| 2 | 缺少凭据/配置(需要人工先登录) |
| 3 | 命令用法错误 |

凭据来源(按优先级):

- workbuddy 域: 桌面端登录后自动写出的凭据文件,`WORKBUDDY_AUTH_FILE`
  可覆盖;新版加密凭据(`sym-v1` 信封)自动调客户端运行时在隐藏子进程里解密。
- qoder 域: 桌面端数据目录(`%APPDATA%\com.qodercn.app*`,`DPAPI+GCM` 双层
  解密),`QODER_TOKEN` 可覆盖。
- trae 域: 桌面端 storage.json(`%APPDATA%\Trae CN\User\globalStorage`,
  `SS_AES` 信封解密,无需 DPAPI 全平台可用),`TRAE_TOKEN` 可覆盖。
- 敏感值(令牌)在一切输出前统一替换为 `[REDACTED]`。

---

## 新增一个域

新增域**不需要**改 `src/auto_signin_kit/cli.py`——它是组合根,只做 entry_points 发现。

1. 复制 `docs/standards/templates/domain/` 到
   `src/auto_signin_kit/domains/<新域名>/`,把里面的占位符和示例域名全局替换成新域名
2. 在 `pyproject.toml` 的 `[project.entry-points."auto_signin_kit.domains"]` 加一行
3. 建配置: `config/domains/<新域名>.yaml` + `config/examples/<新域名>.yaml.example`
4. 补测试: `tests/domains/<新域名>/test_<新域名>.py`
5. `uv sync && uv tool install --editable . --force`
6. 跑门禁 + **手动执行一次真实命令**,不要只看测试绿

---

## 架构约定(硬性规则)

1. **域内部固定三件套:** `schema.py`(域配置)+ `service.py`(纯业务逻辑,
   **禁止 `import typer`**)+ `cli.py`(只做参数解析、调 service、异常映射退出码)。
2. **域与域之间保持独立**;例外显式写进 `pyproject.toml` 的
   `domains 之间彼此独立` 契约的 `ignore_imports`。
3. **配置或用户输入非法就显式报错**,不做静默兜底。
4. **OS 差异只进 `core/adapters/`**(凭据解锁/DPAPI/子进程/注册表/进程枚举都在这),
   业务域禁止平台分支与直接子进程调用(架构门禁强制)。
5. **写操作默认不重试**;只有已确认幂等的接口(签到/查询/活动状态)才开重试。
6. **单文件超过 410 行就拆**(`test_source_file_line_limit` 会拦)。

本仓库的架构与实现文档(as-built,随代码同步维护):

- `docs/superpowers/specs/` — 架构总览(SPEC-01)与各组件实现文档(SPEC-02~05),索引见该目录 README
- `docs/adr/` — 关键架构决策记录(域注册/端口判据/时间预算)
- `docs/standards/` — 开发规范(STD-01~06,回答"应该是什么样")

完整设计说明见 `polydomain-cli` skill 内的
`references/architecture-decisions.md`;已知反模式见同一 skill 的
`references/pitfalls.md`(均为仓库外文件,不在本仓库 `docs/` 下)。

---

## 目录结构

```
src/auto_signin_kit/
├── cli.py                       # 组合根: 只做 entry_points 发现
├── orchestration/               # pipeline.py + step.py(多步命令: 顺序执行 + 逆序回滚)
├── core/
│   ├── ports/                   # 端口层: 只放 Protocol 与数据对象(定时调度)
│   ├── httpclient.py            # 带时间预算+退避重试的请求层(分桶退避)
│   ├── report.py                # 一行 JSON 结果契约 + 脱敏 + 静默日志双通道
│   ├── config/                  # 惰性配置解析 + pydantic 域 schema 基类
│   ├── errors.py                # 异常→退出码体系(1 基类 / 2 配置 / 3 命令 / 4 适配层)
│   ├── logging.py               # 统一走 stderr,保证 stdout 只含业务输出
│   ├── envvar.py                # 环境变量读取: 统一 AUTO_SIGNIN_KIT_* 前缀
│   ├── prompt.py                # 交互确认注入(contextvars,service 层不 import typer)
│   └── adapters/                # OS 差异层
│       ├── native_helper.py     #   JSON-stdin 凭据助手 / 短命令捕获
│       ├── win_dpapi.py         #   DPAPI + CNG AES-GCM
│       ├── client_discovery.py  #   桌面客户端安装定位(注册表/默认目录/mac bundle)
│       ├── win_registry.py      #   卸载注册表项只读扫描
│       ├── win_process.py       #   运行进程 exe 发现
│       ├── log_dir.py           #   每用户可写日志/缓存目录定位
│       └── platform_info.py     #   OS 探测与主机信息(平台分支只在本模块)
└── domains/
    ├── workbuddy/               # 签到 + 成长中心(旅行/任务/补登/兑换/盲盒)
    ├── qoder/                   # 每日 Credits(活动查询/领取/设备风控头)
    ├── trae/                    # 每日签到(status/claim,SS_AES 凭据解锁)
    └── schedule/                # 定时调度: 注册/注销/查看每日自动任务
```

文档地图(逆向取证与实现文档分工):

| 文档 | 性质 |
|------|------|
| `docs/notes/NOTE-01~03` | 逆向取证:端点、信封协议与证据链(索引见 `docs/notes/README.md`) |
| `docs/superpowers/specs/SPEC-01~06` | as-built 实现:代码实际是什么样 |
| `docs/adr/ADR-0001~0003` | 架构决策:为什么这样设计 |

---

## 开发

```bash
uv run ruff check && uv run lint-imports && uv run pytest -q
```

**三个都要绿**。新增域时记得同步更新 `AGENTS.md`。

---

## 许可
