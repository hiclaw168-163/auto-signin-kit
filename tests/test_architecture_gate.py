"""架构门禁: 依赖方向与 domains 层约束。

这些测试不是锦上添花——`test_domain_cli_files_do_not_import_business_modules`
这一类测试对应的是真实发生过的 bug,不是假设的风险。
门禁的价值在于把"教训"变成"机器强制",不用指望所有人(包括未来的你自己
和别的 AI)都记得住每一条约定。
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "auto_signin_kit"
DOMAINS = SRC / "domains"
CORE = SRC / "core"
CLI_PY = SRC / "cli.py"
MAX_FILE_LINES = 410  # 只防新增超标;存量超标时再决定怎么拆,不要事后回填这个数字掩盖问题

FORBIDDEN_OS = ("os.name", "sys.platform", "platform.system(", "os.path.", "os.startfile")
FORBIDDEN_SUBPROCESS = (
    "subprocess.run(",
    "subprocess.Popen(",
    "subprocess.call(",
    "subprocess.check_call(",
    "subprocess.check_output(",
)
FORBIDDEN_RAW_INPUT = "input("

# 域 cli.py 只允许 import 本域这三个模块: service 是业务入口,schema 是配置模型,
# cli 是自身。其余(net/api/auth/credentials/...)都是实现细节。
CLI_ALLOWED_DOMAIN_MODULES = {"cli", "schema", "service"}
# 匹配 `from auto_signin_kit.domains.<域>[.<模块>] import ...` 与 `import ...domains.<域>[.<模块>]`
_DOMAIN_IMPORT_RE = re.compile(
    r"(?:from|import)\s+auto_signin_kit\.domains\.(?P<domain>\w+)"
    r"(?:\.(?P<module>\w+))?"
    r"(?:\s+import\s+(?P<names>[\w, ]+))?"
)


def _py_files(base: Path):
    return sorted(base.rglob("*.py"))


def test_domains_have_no_platform_branches():
    """OS 差异只能出现在 core/adapters/ 下的适配器里,不能泄漏到 domains/。"""
    violations = []
    for f in _py_files(DOMAINS):
        text = f.read_text(encoding="utf-8")
        for token in FORBIDDEN_OS:
            if token in text:
                violations.append(f"{f.relative_to(ROOT)}: {token}")
    assert not violations, f"OS 平台分支泄漏到 domains/: {violations}"


def test_domains_do_not_call_subprocess_directly():
    """domains/ 不直接 subprocess,统一走 core 里封装好的执行函数,方便统一加日志/超时/mock。"""
    violations = []
    for f in _py_files(DOMAINS):
        text = f.read_text(encoding="utf-8")
        for token in FORBIDDEN_SUBPROCESS:
            if token in text:
                violations.append(f"{f.relative_to(ROOT)}: {token}")
    assert not violations, f"domains/ 禁止直接 subprocess: {violations}"


def test_core_does_not_import_domains():
    """核心库不能反向依赖业务域,否则谈不上"核心"。"""
    violations = []
    for f in _py_files(CORE):
        text = f.read_text(encoding="utf-8")
        if "auto_signin_kit.domains" in text:
            violations.append(str(f.relative_to(ROOT)))
    assert not violations, f"core/ 禁止依赖 domains/: {violations}"


def test_ports_do_not_import_adapters():
    """端口只放接口声明,不能反过来依赖具体实现——这条在只有 core/ports/ 目录存在时才有意义。"""
    ports = CORE / "ports"
    if not ports.is_dir():
        return
    violations = []
    for f in _py_files(ports):
        text = f.read_text(encoding="utf-8")
        if "auto_signin_kit.core.adapters" in text:
            violations.append(str(f.relative_to(ROOT)))
    assert not violations, f"ports 禁止依赖 adapters: {violations}"


def test_source_file_line_limit():
    """单文件行数上限,逼近上限时该拆分了,现在拆比涨到报错了再拆更从容。"""
    violations = []
    for f in _py_files(SRC):
        lines = f.read_text(encoding="utf-8").count("\n") + 1
        if lines > MAX_FILE_LINES:
            violations.append(f"{f.relative_to(ROOT)}: {lines} 行")
    assert not violations, f"单文件超过 {MAX_FILE_LINES} 行: {violations}"


def test_cli_is_pure_composition_root():
    """cli.py 只做 entry_points 发现,不手工 import 任何具体域。"""
    text = CLI_PY.read_text(encoding="utf-8")
    assert "from auto_signin_kit.domains" not in text


def test_every_domain_dir_registered_via_entry_points():
    """domains/ 下每个目录都要在 pyproject.toml 里注册 entry point,漏注册会被这里拦下来。"""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    registered = set(
        pyproject.get("project", {})
        .get("entry-points", {})
        .get("auto_signin_kit.domains", {})
        .keys()
    )
    for d in DOMAINS.iterdir():
        if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith("_"):
            assert d.name in registered, (
                f"domains/{d.name} 缺少 entry point — 请在 pyproject.toml "
                '[project.entry-points."auto_signin_kit.domains"] 补一行'
            )


def test_service_no_raw_input():
    """service.py 不直接调用 input(),交互要走 core.prompt 的注入机制。"""
    violations = []
    for f in DOMAINS.rglob("service.py"):
        text = f.read_text(encoding="utf-8")
        if FORBIDDEN_RAW_INPUT in text:
            violations.append(str(f.relative_to(ROOT)))
    assert not violations, f"service 禁止直接 input(): {violations}"


def test_service_modules_do_not_import_typer():
    """service.py 是纯逻辑层,禁止 import typer(交互走 core.prompt 注入)。

    这条规则在实践中曾经反复拦下问题,不是理论上的担心——
    cli.py 混入业务逻辑导致 service 层单测的 mock 覆盖不到真实调用路径,
    是这个架构里目前唯一复发过不止一次的 bug 类型。发现了同类问题,
    照着这个思路加一条对应的门禁测试,不要只手动改完就完事。
    """
    violations = []
    for f in DOMAINS.rglob("service.py"):
        for line in f.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import typer", "from typer")):
                violations.append(f"{f.relative_to(ROOT)}: {stripped}")
    assert not violations, f"service 层混入 typer(交互应走 core.prompt 注入): {violations}"


def test_domain_cli_files_do_not_import_business_modules():
    """域 cli.py 只准 import 本域的 service / schema,不准碰具体实现模块。

    cli 一旦直接 import net/api/auth 这类实现模块,业务逻辑就分叉进了参数解析层:
    service 层单测 mock 的是 service 入口,覆盖不到 cli 里那条真实调用路径。
    与 `test_service_modules_do_not_import_typer` 是同一类问题的两个方向,
    两边都要拦——只拦 service 的话,业务会往 cli 里跑。
    """
    violations = []
    for cli_file in DOMAINS.rglob("cli.py"):
        domain = cli_file.parent.name
        text = cli_file.read_text(encoding="utf-8")
        for match in _DOMAIN_IMPORT_RE.finditer(text):
            # 跨域 import 由 import-linter 的 independence 契约管,这里只看本域
            if match.group("domain") != domain:
                continue
            if match.group("module"):
                # from ...domains.<域>.<模块> import <符号>: 符号是模块内成员,不参与判定
                imported = [match.group("module")]
            elif match.group("names"):
                imported = [n.strip() for n in match.group("names").split(",")]
            else:
                imported = []
            for name in imported:
                if name and name not in CLI_ALLOWED_DOMAIN_MODULES:
                    violations.append(f"{cli_file.relative_to(ROOT)}: {name}")
    assert not violations, (
        f"域 cli.py 只允许 import {sorted(CLI_ALLOWED_DOMAIN_MODULES)},"
        f"发现业务模块(业务应留在 service 层): {violations}"
    )
