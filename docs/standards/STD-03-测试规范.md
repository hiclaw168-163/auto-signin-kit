# 测试规范

- 分层: `tests/core/`(公共库)、`tests/core/adapters/`(适配器,按 OS `skipif`)、
  `tests/domains/`(命令)、`tests/test_cli.py`(组合根入口)、
  `tests/test_architecture_gate.py`(架构门禁)。
- 命令测试用 `typer.testing.CliRunner.invoke()`;外部依赖(子进程/网络/文件系统)
  一律 monkeypatch,不测真网络。
- **配置隔离**: 配置目录解析是惰性函数,测试里
  `monkeypatch.setattr(config_module, "resolve_config_dir", lambda: tmp_path)`
  后在 `tmp_path` 写临时 yaml。不要去 patch 模块级常量——骨架里没有这种常量,
  自己新加代码时也不要引入(见 STD-02)。
- 协议/端口依赖用参数注入: service 函数收 `xxx: Protocol | None = None`,
  缺省时惰性取工厂;测试传桩对象(Protocol 可鸭子类型替换,无需继承)。
- 需要**真外部系统**的用例(真实推送/真数据库)标 `@pytest.mark.integration`
  (marker 已在 `pyproject.toml` 注册),默认不跑,避免门禁依赖外部状态。
- 用例名 = 被测行为: `test_<被测行为>`(`test_render_rejects_unknown_style`),
  不写 `test_1`/`test_ok`。
- 覆盖基线,每个命令至少:
  - 正常流一次
  - `--help` 可用、`-y` 旗标生效(非交互不卡确认)
  - 配置/输入非法时的报错路径(exit code 正确)
  - schema 拒绝未知字段(extra="forbid" 生效)
- 渲染/视觉输出类功能,测试绿 ≠ 输出对: 门禁之外要**人工看一眼产物**
  (基线比对: 改版式前记录页数/尺寸,改后断言不变)。

## 全量门禁

```bash
uv run ruff check && uv run lint-imports && uv run pytest -q
```

只增不减,必须全绿。Windows 控制台下 `lint-imports` 偶发 rich 渲染的
`UnicodeEncodeError`(GBK 编不了 spinner 字符),与代码无关,加 `PYTHONUTF8=1` 重跑。

## 架构门禁(`tests/test_architecture_gate.py`)

骨架自带,改模板/加新约束时同步维护:

- OS 分支泄漏进 domains / domains 直调 subprocess / core 反向依赖 domains
- ports 依赖 adapters
- 单文件行数上限(`MAX_FILE_LINES`)
- cli.py 混入业务 import(`test_domain_cli_files_do_not_import_business_modules`)
- service 层混入 typer、直接 `input(`(`test_service_modules_do_not_import_typer`)
- domains/ 目录与 entry_points 注册一一对应

**新发现一类重复犯的错 → 加一条对应门禁测试**,不要只手动改完就完事;
门禁的价值就是把教训变成机器强制。
