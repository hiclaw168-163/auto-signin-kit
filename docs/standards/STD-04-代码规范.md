# 代码规范

以 PEP8 + 常见工程规约为基线,原则是**规约用工具固化**:
`pyproject.toml` 的 ruff 规则集就是规范的可执行部分,
门禁 `uv run ruff check && uv run pytest`。目标是"违反即失败",不靠自觉和评审嘴。

## 分级

| 级别 | 含义 | 落地方式 |
|------|------|----------|
| 强制 | 违反即门禁失败 | ruff 规则集 + pytest 门禁 |
| 推荐 | 评审把关 | 本文约束,不进门禁 |
| 参考 | 建议遵循 | 本文说明 |

## 一、格式(强制)

- 4 空格缩进,禁止 Tab;行宽 100(`line-length = 100`);文件末尾一个换行;UTF-8 + LF。
- 导入分组: 标准库 → 第三方 → 本地,组间空行(ruff `I` 强制)。
- 顶层定义前后两个空行,类内方法之间一个空行。

对应规则: `E` `W` `I`。

## 二、命名(强制)

| 对象 | 风格 | 示例 |
|------|------|------|
| 包 / 模块 | 小写(可下划线) | `load_config.py` |
| 类 | CapWords;异常类以 `Error` 结尾 | `ConfigError`、`RenderSpec` |
| 函数 / 变量 | snake_case | `load_domain_config_model` |
| 常量 | 模块级 UPPER_SNAKE_CASE | `MAX_FILE_LINES` |

- 禁止拼音与英文混用、禁止无意义缩写;避免 `l` / `O` / `I` 单字符名。
- 常量必须定义在模块级并全大写;函数内不要出现全大写"伪常量"。

## 三、文档注释(强制 + 推荐)

- **强制**: 公共模块、类、函数必须有 docstring(测试代码豁免)。
- docstring 写"做什么、为什么、异常/边界";注释解释"为什么",不重复代码。
- 禁止保留注释掉的代码块;无用注释随代码一并删除。

对应规则: `D1`(门禁只要求"必须有 docstring";中文 docstring 结尾加句号是**推荐项**,D4xx 不进门禁)。

## 四、异常处理(强制)

- **禁止 `try-except-pass`**: 捕获即需处理,至少 `log_warning` 说明为何忽略;
  确实要忽略用 `contextlib.suppress(...)` 并注释原因。
- 异常链完整: `raise ... from err`;不需暴露链时 `raise ... from None`。
- 不用异常做流程控制;业务异常统一 `core.errors` 子类,由组合根转退出码。
- 抛给上层处理时**不要**先 `log_error` 一遍,避免同一错误打印两次。

对应规则: `S110`、`B904`。

## 五、日志(强制)

- 日志统一走 `core.logging`(`log_debug`/`log_info`/`log_warning`/`log_error`/`log_success`)。
- **禁止 `print` 调试输出**;日志走 stderr,业务输出走 stdout
  (保证 `eval "$(cli ...)"` 之类的外壳只捕获业务输出)。
- 日志内容要能定位: 含对象名、路径、退出码、失败原因。

对应规则: `T20`。

## 六、对象建模(公共模型强制)

**结构固定的多字段数据、存在多种实现的职责,必须显式建模**,禁止裸 `dict` 跨函数搬运散装字段。

| 场景 | 用什么 |
|------|--------|
| 结构固定的值 / 配置 / 上下文 | `@dataclass(frozen=True)` / pydantic `DomainConfig` |
| 多种实现、同一行为契约 | `typing.Protocol`(ports)+ 具体实现(adapters) |
| 需自定义异常 | 继承 `core.errors` 子类 |
| 无状态纯流程 | 模块级函数,不强行造类 |

强制项:

1. 跨两个及以上函数共享的字段组必须建模为 `@dataclass`,禁止 `dict[str, Any]` 散传。
2. dataclass 优先 `frozen=True`;可变默认值用 `field(default_factory=...)`。
3. 解析与校验收口在 `@classmethod from_dict()` / pydantic 模型,
   命令函数内不逐字段 `cfg.get()` + 判空。
4. 行为多态必须走 Protocol/抽象基类;禁止 `if platform == "..."` 散落分支(见 STD-05)。

## 七、行为差异做成配置,不做代码分支

- 同一种产物的"样子"差异(行列数、颜色、开关、列序),收敛为域 YAML 字段,
  由 schema 校验后生效;**加一种"样子"优先写配置,不写渲染代码**。
- 只有出现全新能力(新原语/新外部系统)才动 core。
- 配置项必须真正接线: 每个字段都要落到消费代码里,加"死配置"(声明了不生效)
  跟加错配置一样是 bug——为纯函数样式映射写字段级断言测试,防止退化。

## 八、豁免与例外

- 豁免写在 `pyproject.toml` 的 `per-file-ignores`(或行内 `# noqa: <规则>` + 原因注释),
  **禁止**直接放宽全局规则集。
- 骨架默认豁免: `domains/*/__init__.py` 的 `E402`(域 app 定义后 import 命令模块)。
