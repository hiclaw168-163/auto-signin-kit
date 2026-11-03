"""scaffold 生成的新域骨架模板(SPEC-11 阶段二)。

模板全部引用 core.kit 内核(阶段一产物),桩阶段即可运行、契约测试
以 xfail 标记提醒开发者填写签到逻辑。两类模板:
``web``(HTTP 签到)与 ``client``(本地客户端签到)的差异收敛在
schema/api 两处,其余共用。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vars:
    """模板变量集(name 为合法小写 python 标识符)。"""

    name: str
    kind: str  # web | client
    display: str
    base_url: str
    daily_time: str

    @property
    def cls(self) -> str:
        """域名 → 配置类前缀,如 ``my_app`` → ``MyApp``。"""
        return "".join(p.capitalize() for p in self.name.split("_"))


def _init_py(v: Vars) -> str:
    return f'''"""{v.display} 域命令。(scaffold 生成)"""

from auto_signin_kit.domains.{v.name}.cli import app

__all__ = ["app"]
'''


def _schema_py(v: Vars) -> str:
    extra = ""
    if v.kind == "client":
        extra = (
            "    #: 客户端本地数据目录;留空 = 自动探测(参考 domains/doubao 的探测实现)。\n"
            "    user_data_dir: str | None = None\n"
        )
    return f'''"""{v.display} 域配置,对应 ``config/domains/{v.name}.yaml``。(scaffold 生成)"""

from __future__ import annotations

from auto_signin_kit.core.config.schema import BudgetedConfig

#: {v.display} 接口宿主({v.name.upper()}_BASE_URL 环境变量优先)。
DEFAULT_BASE_URL = "{v.base_url}"


class {v.cls}DomainConfig(BudgetedConfig):
    """{v.name} 域配置项;``extra="forbid"``,未知字段直接报错。

    ``budget_seconds`` 继承自 BudgetedConfig(240 秒上限口径)。
    """

    base_url: str = DEFAULT_BASE_URL
{extra}'''


def _api_py(v: Vars) -> str:
    if v.kind == "client":
        return f'''"""{v.display} 域 API 封装。(scaffold 生成;客户端签到参考 domains/doubao)

客户端签到的通用路径: 定位客户端数据目录 → 解密 Cookie/登录态
(平台差异收口 core/adapters)→ 必要时 CDP 兜底。按目标客户端实现。
"""

from __future__ import annotations

from auto_signin_kit.core.httpclient import Budget


class Api:
    """{v.display} 签到 API;端点契约与凭证获取在此填写。"""

    def __init__(self, base_url: str, token: str | None, budget: Budget) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token  # client 类域通常不用 token,凭证走本地解密
        self.budget = budget

    def claim(self) -> tuple[int, object, str | None]:
        """执行签到;返回 (http状态, 响应体, 错误)。"""
        raise NotImplementedError("TODO(开发者): 填写 {v.display} 签到端点与凭证链路")
'''
    return f'''"""{v.display} 域 API 封装。(scaffold 生成;端点契约在此填写)"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.httpclient import Budget, call


class Api:
    """{v.display} 签到 API;用浏览器开发者工具抓一次签到请求即可填完。"""

    def __init__(self, base_url: str, token: str | None, budget: Budget) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.budget = budget

    def _headers(self) -> dict[str, str]:
        # TODO(开发者): 按目标站点的鉴权方式补 Cookie/Authorization 头
        headers = {{"content-type": "application/json"}}
        if self.token:
            headers["authorization"] = "Bearer %s" % self.token
        return headers

    def claim(self) -> tuple[int, Any, str | None]:
        """执行签到;返回 (http状态, 响应体, 错误)。"""
        # TODO(开发者): 换成真实签到端点(参考 domains/trae/api.py 的 call 用法)
        raise NotImplementedError("填写 {v.display} 签到端点")

    def status(self) -> tuple[int, Any, str | None]:
        """查询签到状态(只读);TODO(开发者): 换成真实查询端点。"""
        raise NotImplementedError("填写 {v.display} 状态端点")
'''


def _service_py(v: Vars) -> str:
    return f'''"""{v.name} 域业务编排。(scaffold 生成桩;在四个钩子里填签到逻辑)

流程外壳(命令分发/异常映射/台账输出/退出码)由 ``SigninDomain`` 基类锁定,
本文件只剩四个填空题: build(凭证→API) / claim(签到) / status(查询) /
doctor(离线体检)。桩阶段全部动作输出"未实现"(退出码 2),CLI/UI/调度
链路即可运行;填完钩子后本域即接入台账与调度。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.core.kit.domain import DomainError, SigninDomain
from auto_signin_kit.core.report import make_result
from auto_signin_kit.domains.{v.name}.api import Api
from auto_signin_kit.domains.{v.name}.schema import {v.cls}DomainConfig

_STUB_REPORT = (
    "脚手架桩未实现: 编辑 domains/{v.name}/service.py 填写四个钩子,"
    "完成后处理 tests/domains/{v.name}/test_contract.py 的 xfail 标记"
)


def _stub(result: str = "ERROR") -> tuple[dict, int]:
    return make_result(result, _STUB_REPORT, needs_attention=True,
                       domain="{v.name}"), 2


class {v.cls}Domain(SigninDomain):
    """{v.display} 签到域;四钩子之外的一切由基类保证。"""

    name = "{v.name}"
    display = "{v.display}"

    def build(self, config: {v.cls}DomainConfig, *, once: bool = False,
              dry_run: bool = False) -> Api:
        """凭证 → API 客户端;TODO(开发者): 取凭证(配置/环境变量/客户端解密),
        失败时 ``raise DomainError("NO_AUTH", 提示, exit_code=2)``。"""
        return Api(config.base_url, None, Budget(config.budget_seconds or 240.0))

    def claim(self, api: Api, config: {v.cls}DomainConfig, *, dry_run: bool = False,
              once: bool = False) -> tuple[dict, int]:
        """执行一轮签到;TODO(开发者): 调 api.claim(),按响应判定
        CLAIMED/ALREADY/失败(参考 trae/qoder 的传输与业务码映射)。"""
        return _stub()

    def status(self, api: Api, config: {v.cls}DomainConfig) -> tuple[dict, int]:
        """只查签到状态,不修改;TODO(开发者): 调 api.status()。"""
        return _stub()

    def doctor(self, config: {v.cls}DomainConfig) -> tuple[dict, int]:
        """离线体检(不联网);TODO(开发者): 校验凭证可读性/有效期。"""
        return _stub()


domain = {v.cls}Domain()
#: 模块级入口(CLI 工厂与调度引用): 即模板方法绑定。
run_action = domain.run_action
'''


def _schedule_meta_py(v: Vars) -> str:
    return f'''"""{v.display} 的调度声明: schedule 域经 entry-point 注册表发现。(scaffold 生成)"""

from __future__ import annotations

from auto_signin_kit.core.ports.scheduler import ScheduleSpec

#: 域类别: ``web``(HTTP 签到)或 ``client``(本地客户端签到)。
KIND = "{v.kind}"
DISPLAY_NAME = "{v.display}"


def ready() -> bool:
    """就绪探测: 桩阶段恒 True;client 类接入客户端探测后改为真实判定。"""
    return True


SPECS: tuple[ScheduleSpec, ...] = (
    ScheduleSpec(
        name="{v.cls}DailyCheckin",
        argv=("auto-signin-kit", "{v.name}", "auto", "--silent"),
        times=("{v.daily_time}",),
        limit_minutes=5,
        description="{v.display} 每日签到(scaffold 生成;触发时间按需调整)",
    ),
)
'''


def _cli_py(v: Vars) -> str:
    return f'''"""{v.name} 域命令行入口: 参数解析 + 调 service + 异常映射退出码。(scaffold 生成)"""

from __future__ import annotations

from auto_signin_kit.core.kit.cli import create_domain_app
from auto_signin_kit.domains.{v.name} import service
from auto_signin_kit.domains.{v.name}.schema import {v.cls}DomainConfig

app = create_domain_app("{v.name}", "{v.display}", {v.cls}DomainConfig, service)
'''


def _config_yaml(v: Vars, example: bool) -> str:
    head = ("# 示例配置,复制为 config/domains/%s.yaml 后使用。\n" % v.name
            if example else "# %s 域配置(scaffold 生成;本地覆盖写同目录 %s.local.yaml)\n"
            % (v.display, v.name))
    return head + f"base_url: {v.base_url}\n"


def _test_contract_py(v: Vars) -> str:
    return f'''"""{v.name} 域契约测试。(scaffold 生成)

桩阶段: ``test_claim_implemented`` 以严格 xfail 标记——实现签到逻辑后
它会 XPASS 并让门禁报错,提醒你删掉标记、把真实断言补上。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from auto_signin_kit.domains.{v.name}.cli import app
from auto_signin_kit.domains.{v.name}.schema import {v.cls}DomainConfig
from auto_signin_kit.domains.{v.name}.service import run_action

runner = CliRunner()


def _config() -> {v.cls}DomainConfig:
    return {v.cls}DomainConfig()


def test_app_has_standard_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("auto", "status", "doctor"):
        assert cmd in result.output


def test_unknown_action_returns_2():
    assert run_action("nope", _config()) == 2


def test_doctor_runs_without_network():
    assert run_action("doctor", _config()) == 2  # 桩: 未实现也是 2


@pytest.mark.xfail(reason="scaffold 桩未实现;实现后 XPASS,删标记并补真实断言", strict=True)
def test_claim_implemented():
    assert run_action("claim", _config()) == 0
'''


def render(v: Vars) -> dict[str, str]:
    """变量集 → {相对路径: 文件内容}(路径相对项目根)。"""
    d = "src/auto_signin_kit/domains/%s" % v.name
    files = {
        "%s/__init__.py" % d: _init_py(v),
        "%s/schema.py" % d: _schema_py(v),
        "%s/api.py" % d: _api_py(v),
        "%s/service.py" % d: _service_py(v),
        "%s/cli.py" % d: _cli_py(v),
        "%s/schedule_meta.py" % d: _schedule_meta_py(v),
        "config/domains/%s.yaml" % v.name: _config_yaml(v, example=False),
        "config/examples/%s.yaml.example" % v.name: _config_yaml(v, example=True),
        "tests/domains/%s/test_contract.py" % v.name: _test_contract_py(v),
    }
    return files
