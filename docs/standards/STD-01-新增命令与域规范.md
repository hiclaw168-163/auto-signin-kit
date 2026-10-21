# 新增命令与域规范

## 命名

- 域名: 英文小写单数名词,按**职责/版式/能力**命名,不按语种或实现命名
  (反例: 用 `english` 命名英语抄写——换一种内容就没法复用)。
- 命令名: kebab-case(`commit-merge`);命令函数所在模块 snake_case 与之对应。
- CLI 名与 Python 包名解耦: 包名含 `_` 时 CLI 命令用 `-`(`my_tool` → `my-tool`),
  由 `pyproject.toml` 的 `[project.scripts]` 决定。

## 结构(强制三件套)

```
src/auto_signin_kit/domains/<域>/
  __init__.py   # app = typer.Typer(help=...);底部 import 命令模块(# noqa: E402,F401)
  schema.py     # 域配置,继承 DomainConfig(extra="forbid")
  service.py    # 纯业务逻辑;禁止 import typer;交互走 core.prompt
  cli.py        # 只做参数解析 → set_prompt → 调 service → finally: set_prompt(None)
```

- 复制 `docs/standards/templates/domain/` 下的模板起步,不要凭记忆重写。
- `cli.py` docstring 首行写 `auto-signin-kit <域> <命令> — <功能>`。
- 每个命令必备 `--help` 与非交互 `--yes/-y`;涉及覆盖/删除/发布类动作时 `-y` 是唯一跳过确认的方式。
- 失败抛 `core.errors` 子类,由组合根统一转退出码(`ConfigError`=2 / `CommandError`=3,
  新错误类型从 5 开始加,不复用;1/2/3/4 已分别归基类/`ConfigError`/`CommandError`
  /`AdapterError`)。干净早退在 cli 层用 `raise typer.Exit(0)`。

## 注册清单

1. `pyproject.toml` 的 `[project.entry-points."auto_signin_kit.domains"]` 加一行
   `<域> = "auto_signin_kit.domains.<域>:app"`
2. `uv sync && uv tool install --editable . --force`(entry_points 写在 dist-info,
   只改 pyproject 不重装不生效——漏了这步新命令不可见,是最常见的坑)
3. 漏注册会被 `tests/test_architecture_gate.py::test_every_domain_dir_registered_via_entry_points` 拦下
4. `src/auto_signin_kit/cli.py` 是纯组合根,禁止手工 `add_typer`(门禁强制)

## 编排(跨域调用)

- 跨命令流水线用 `orchestration.Pipeline` / `CallableStep`,每步一个 `CallableStep`,
  失败按完成序逆序 rollback;不要写一个大函数手动处理每步失败。
- 编排命令优先调其他域的**公开 service 函数**,不深入对方私有模块。
- 失败转 `CommandError` / `PipelineError` 或向上抛出;禁止静默吞掉非零退出。

## 新域端到端清单

| # | 步骤 | 说明 |
|---|------|------|
| 1 | (按需)core 契约 | 仅当新域需要**新的平台能力或跨域基础设施**时,才动 `core/ports/` + `core/adapters/`,见 STD-05 |
| 2 | 域三件套 | 复制 `docs/standards/templates/domain/`,替换占位业务逻辑 |
| 3 | 配置模板 | `config/examples/<域>.yaml.example` + `config/domains/<域>.yaml`,见 STD-02 |
| 4 | 注册 | pyproject entry_points 一行 + `uv sync && uv tool install --editable . --force` |
| 5 | 测试 | `tests/domains/<域>/`,分层与写法见 STD-03;`--help` 与 `-y` 旗标必须有用例 |
| 6 | 文档同步 | `AGENTS.md`(若规则变化)、`docs/standards/README.md`(若引入新形态)、架构 spec(若有) |
| 7 | 门禁 | `uv run ruff check && uv run lint-imports && uv run pytest -q` 全绿后提交 |

提交信息: `<type>(<scope>): <subject>`,如 `feat(invoice): 新增发票解析域`。
