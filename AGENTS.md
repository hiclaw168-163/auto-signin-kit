# AGENTS.md

给在这个仓库里工作的 AI(和人)的硬性规则。本文件只留**最容易被违反**的条款;
各主题细则(怎么落地/为什么)在 `docs/standards/`,索引
`docs/standards/README.md`——新增命令前先过一遍 `STD-01` 的端到端清单。

## 硬性规则

1. **域注册用 entry_points**,在 `pyproject.toml` 的
   `[project.entry-points."auto_signin_kit.domains"]` 加一行,`cli.py` 永远不手工
   `add_typer`。加完要 `uv sync && uv tool install --editable . --force`
   才会生效(entry_points 写在 dist-info,改 pyproject.toml 不自动刷新)。

2. **每个域内部固定三件套**: `schema.py`(域配置,继承
   `core.config.schema.DomainConfig`)+ `service.py`(纯业务逻辑,**禁止
   `import typer`**,交互走 `core.prompt` 注入)+ `cli.py`(只做参数解析、
   调用 service、把异常映射成退出码)。`tests/test_architecture_gate.py` 里
   `test_domain_cli_files_do_not_import_business_modules` 拦 cli 混入业务模块,
   `test_service_modules_do_not_import_typer` 拦 service 混入 typer,不是靠自觉。

3. **域与域之间保持独立**,不要互相 import。真有业务需要的跨域调用
   (比如"提交后触发部署"),要显式写进 `pyproject.toml` 的
   `domains 之间彼此独立` 契约的 `ignore_imports` 里,不能悄悄发生。

4. **配置或用户输入非法就显式报错,不做静默兜底**。`core.config` 的
   `DomainConfig` 用 `extra="forbid"`,配置里有 schema 之外的字段直接报错。

5. **只在真的需要"第二种实现"时才定义端口(Ports & Adapters)**。判断标准:
   这类外部依赖以后会不会真的出现第二种实现?会,就在 `core/ports/` 定义
   `Protocol` + `core/adapters/` 放具体实现;这辈子只有一种实现的东西不套
   模式——套用模式不是免费的,过度抽象跟直接写死一样是维护负担。

6. **多步骤/需要失败回滚的命令用 `orchestration.Pipeline`**,不要写成一个
   大函数手动处理每一步失败该怎么办。

7. **单文件超过 410 行就该拆了**,`test_source_file_line_limit` 会提醒你,
   逼近上限时主动拆,不要等报错了才拆。

## 门禁

```bash
uv run ruff check && uv run lint-imports && uv run pytest -q
```

每次改动后跑一遍,三个都要绿。`lint-imports` 报错时,先看是不是真实的
架构违规,不是就检查有没有排除该排除的目录/加该加的 `ignore_imports`。

## 新增一个域的步骤

细则与完整清单见 `docs/standards/STD-01-新增命令与域规范.md`,速查:

1. `src/auto_signin_kit/domains/<域名>/{__init__.py,schema.py,service.py,cli.py}`
   —— 复制 `docs/standards/templates/domain/` 下的模板改
2. `pyproject.toml` 的 entry-points 加一行
3. `config/domains/<域名>.yaml` + `config/examples/<域名>.yaml.example`
4. `tests/domains/<域名>/` 补测试
5. `uv sync && uv tool install --editable . --force`
6. 跑一遍门禁 + 手动执行一次命令,不要只看测试绿——涉及渲染/视觉输出的功能,
   测试绿不代表"看起来是对的",这个坑真实存在。
7. 引入了新的配置/文档形态时,同步 `docs/standards/README.md` 索引
