# GUIDE-02 · 新增一个域

> 要支持一个新的助手 / 站点时照着做。前提是已经读完
> [GUIDE-01](GUIDE-01-开发者上手.md);规范细则见
> [STD-01](../standards/STD-01-新增命令与域规范.md)。

## 0. 先确认是不是真要开域

| 情况 | 做法 |
|------|------|
| 一个新的助手/站点,有自己的凭据与接口 | **开新域**(本文) |
| 只是给已有域加一个动作 | 在该域 `cli.py` 加命令 + `service.py` 加分支,不开域 |
| 只是换个触发时间 | 改 `config/domains/schedule.yaml`,不动代码 |

新域的判据是"**有独立的一套凭据与端点**",不是"功能看起来不一样"。

## 1. 复制模板

```bash
cp -r docs/standards/templates/domain src/auto_signin_kit/domains/<新域名>/
```

然后把模板里的占位符与示例域名(`workbuddy`)全局替换成新域名。**`__init__.py`
的 docstring 一定要改**——模板故意用 `<域>` 占位,如果复制后它还写着某个具体
域名,说明漏改(历史上真发生过: 看着像"已填写"就放过去了)。

## 2. 注册 entry_points

`pyproject.toml`:

```toml
[project.entry-points."auto_signin_kit.domains"]
<新域名> = "auto_signin_kit.domains.<新域名>:app"
```

**组合根 `cli.py` 不用改**,它只做发现。但必须重新装:

```bash
uv sync && uv tool install --editable . --force
```

## 3. 建配置

- `config/domains/<新域名>.yaml` — 入库默认值
- `config/examples/<新域名>.yaml.example` — 给用户抄的样例

配置模型写在域的 `schema.py` 里,继承 `core.config.schema.DomainConfig`,
`extra="forbid"`(多写键直接报错)。本机差异放
`config/domains/<新域名>.local.yaml`,不入库。

## 4. 填三件套

| 文件 | 必须 | 禁止 |
|------|------|------|
| `schema.py` | 域配置模型 | — |
| `service.py` | 纯业务逻辑 | **禁止 `import typer`**(有门禁);交互要确认走 `core.prompt` 注入 |
| `cli.py` | 参数解析 + 调 service + 异常映射退出码 | 业务逻辑;直接 import 域内实现模块(`net`/`api`/`auth` 等) |

最后一条有门禁 `test_domain_cli_files_do_not_import_business_modules` 守着:
`cli.py` 只允许 import 本域的 `cli` / `schema` / `service`。业务一旦分叉进 cli,
service 层的单测就覆盖不到那条真实调用路径。

数据放哪按 [STD-06 §3](../standards/STD-06-脚本与资产规范.md) 的判据:
域私有的小型常量目录 → 域内 `data.py`(零 IO);用户可改 → `config/`;
用户不该随手改 → 源码常量。

## 5. OS 差异放对地方

业务域里**不准出现平台分支和直接子进程调用**(架构门禁强制):

- 只有一种实现 → `core/adapters/`
- 有多种真实实现(像定时调度的 schtasks / launchd / cron)→ 先在
  `core/ports/` 定 Protocol,再在 `core/adapters/` 写实现;端口只放形状,
  由 import-linter 的 `ports 不依赖 adapters` 契约强制

## 6. 补测试

`tests/domains/<新域名>/test_<新域名>.py`,至少覆盖:

- 无凭据路径(应给 `NO_AUTH` + 退出码 2,并带上查过的路径 `looked_in`)
- 配置非法(`extra="forbid"` 生效: 未知键报错)
- 纯函数的业务判定(不联网也能测的那部分)
- 时间预算 / 重试策略这类常量如果有联动,写成断言防回归

## 7. 要每天自动跑就接调度

在 `domains/schedule/data.py` 的 `TASKS` 加一条:

```python
ScheduleSpec(
    name="<新域名>Daily",
    argv=("auto-signin-kit", "<新域名>", "auto", "--silent"),
    times=("09:05", "13:05"),   # 一天多个: 幂等领取多跑不会多领,用来兜单点失败
    limit_minutes=5,
    description="……",
)
```

然后同步 `config/domains/schedule.yaml` 的 `tasks`(写不写都行,不写就沿用这里的
默认值)。**时限必须大于该域自己的时间预算**——否则任务会在脚本收尾前被平台强杀,
丢掉"已领到的部分"的日志。这条由
`test_execution_limit_covers_domain_budget` 守着。

## 8. 检查清单(提交前过一遍)

- [ ] `uv run ruff check && uv run lint-imports && uv run pytest -q` 三条全绿
- [ ] **手动真实执行过一次命令**(测试绿不代表行为对)
- [ ] `pyproject.toml` 的 entry_points 已加,且重装过
- [ ] `config/domains/<新域名>.yaml` 与 `.example` 都在
- [ ] 域 `__init__.py` 的 docstring 不是模板占位
- [ ] 新增了 SPEC(或更新了对应 SPEC),并同步 `docs/superpowers/specs/README.md` 索引
- [ ] 逆向到的端点写进了 `docs/notes/`,同步该目录 README
- [ ] 根 `README.md` 的目录树与命令列表已更新
