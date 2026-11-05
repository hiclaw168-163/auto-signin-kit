"""应用档案的枚举常量与展示文案(全库统一口径)。

类别/版本区/来源这三组值散落在 schema 默认值、UI 标签、脚手架模板、
文档里,曾经各写各的导致"网页版签/客户端签到"两种叫法并存——收拢成
唯一事实源: 后端从这里取,前端 ``frontend/src/constants.ts`` 镜像同一份
(改值时两边一起改,门禁测试拦漂移)。

分类标准(2026-11-06 与用户对齐): **按凭据来源**——凭据来自本地客户端
的(ss_aes 解锁桌面端存储 / chromium_cookies / CDP)都是 ``client``;
凭据由人手动维护(令牌文件/环境变量)的才是 ``web``。签到动作是 HTTP
还是 CDP 不作为分类依据。
"""

from __future__ import annotations

#: 应用类别(凭据来源口径)。
KIND_WEB = "web"
KIND_CLIENT = "client"
KINDS = (KIND_WEB, KIND_CLIENT)

#: 类别 → 展示文案(短文案用于表格标签,长文案用于表单选项/文档)。
KIND_SHORT_LABELS = {KIND_WEB: "网页版签", KIND_CLIENT: "客户端签到"}
KIND_LABELS = {
    KIND_WEB: "网页版签(令牌文件/环境变量)",
    KIND_CLIENT: "客户端签到(本地客户端/CDP)",
}

#: 版本区(ask_apps.region)。
REGION_CN = "cn"
REGION_INTL = "intl"
REGION_LABELS = {REGION_CN: "国内版", REGION_INTL: "国际版"}

#: 行来源(ask_apps.source)。
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
SOURCE_LABELS = {SOURCE_AUTO: "自动同步", SOURCE_MANUAL: "手动新建"}

#: 积分刷新时间的形状校验(HH:MM;ask_apps.credits_refresh_time 与
#: 应用声明共用)。过了这个时刻才算进入"新一天的积分周期"。
DEFAULT_CREDITS_REFRESH_TIME = "00:00"


def valid_refresh_time(value: str) -> bool:
    """``HH:MM`` 形状 + 数值范围校验(调度时间同款判定,不引入正则依赖)。"""
    if not (len(value) == 5 and value[2] == ":"
            and value[:2].isdigit() and value[3:].isdigit()):
        return False
    return int(value[:2]) <= 23 and int(value[3:]) <= 59
