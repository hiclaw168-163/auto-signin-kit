"""Qoder 两版客户端的应用身份: 一份领取实现,两个对等的注册键。

国际版与国内版是**本机两个真实存在的客户端**——各自的数据目录、mac Keychain
条目、API 域、环境变量前缀都不同;而 campaigns/claim 协议同构(NOTE-05 实测)。
应用同步按"装了哪个客户端"建 ``ask_apps`` 主档,关联键是 ``app_code = 台账
domain 列``,所以两版各占一行、各自调度,业务代码共用本包:

- :data:`INTL` → 域 ``qoder``(存量域,原「Qoder CN」语义已迁到 ``qoder_cn``)
- :data:`CN`   → 域 ``qoder_cn``(与 ``trae_cn``/``workbuddy_cn`` 同后缀约定)

同版多登录(一个客户端两个账号)仍走 SPEC-12 的具名账户 ``--account``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QoderProfile:
    """一个 Qoder 客户端身份 = 一个域;值都是"这个客户端叫什么/在哪"。"""

    app_code: str
    display: str
    region: str
    #: Electron ``userData`` 目录名模式(bundle id),Windows/macOS 同一约定。
    #: 国际版 Windows 目录名按命名惯例推定,尚未在 Windows 实测(见 NOTE-05)。
    auth_globs: tuple[str, ...]
    #: 该身份的专属环境变量前缀: ``QODER_TOKEN`` / ``QODER_CN_TOKEN``。
    env_prefix: str
    #: macOS 的 Electron safeStorage Keychain 条目名(2026-11-22 双客户端实测)。
    mac_keychain_service: str

    def env(self, suffix: str) -> str:
        """专属环境变量名(``TOKEN``/``AUTH_DIR``/``BASE_URL``/``SIGNIN_LOG``…)。"""
        return "%s_%s" % (self.env_prefix, suffix)


INTL = QoderProfile(
    app_code="qoder",
    display="Qoder",
    region="intl",
    auth_globs=("com.qoder.app*",),
    env_prefix="QODER",
    mac_keychain_service="Qoder App Safe Storage",
)
CN = QoderProfile(
    app_code="qoder_cn",
    display="Qoder 国内版",
    region="cn",
    auth_globs=("com.qodercn.app*",),
    env_prefix="QODER_CN",
    mac_keychain_service="Qoder CN App Safe Storage",
)


def profile_of_dir(data_dir: Path) -> QoderProfile:
    """按数据目录名认出这是哪一版客户端(bundle id 含 ``qodercn`` = 国内版)。

    目录名是稳定充分的信号(Keychain 条目名与客户端命名都跟着它走);
    不为此解锁登录态——Keychain 探测在 UI 同步路径里会打扰用户。
    """
    return CN if "qodercn" in data_dir.name.lower() else INTL
