# ADR-0001-域注册用 entry-points,cli.py 永远做纯组合根

- **状态**: 已接受并实现
- **日期**: 2026-10(项目伊始)
- **关联**: SPEC-01 §3 · STD-01 · `tests/test_architecture_gate.py::test_cli_is_pure_composition_root`

## 背景

多域 CLI 的两种组织方式:

1. **手工注册**: `cli.py` 里 `app.add_typer(qoder_app, name="qoder")` 一条条加。
2. **声明式注册**: 每个域自带 typer `app`,在 pyproject 的
   `[project.entry-points."auto_signin_kit.domains"]` 声明,组合根运行时枚举。

## 决策

采用 entry-points 声明式注册。

- 新增域 = 在 pyproject 加一行 + 建域目录,`src/auto_signin_kit/cli.py` **零修改**;
- 组合根永远不 import 任何具体域——这是机器强制的
  (gate 测试断言文本 `from auto_signin_kit.domains` 不存在);
- `templates/domain`(docs/standards)的 `cli.py` 里直接写 `app = typer.Typer(...)`,
  新域就是可挂载单元。

## 结果与代价

- 好处: 域完全自治、组合根不会成为"每次合并都冲突"的文件;
  域可以整体删掉,组合根与测试都无感知。
- 代价(必须写进报错信息): entry_points 物化在 dist-info,
  **改 pyproject 不重装不生效**——`uv sync && uv tool install --editable . --force`;
  cli.py 对空注册给出带这条提示的 RuntimeError。
- 测试形态: `test_every_domain_dir_registered_via_entry_points`
  从另一个方向兜底(建了域没注册也会被拦)。
