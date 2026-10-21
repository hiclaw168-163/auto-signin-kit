"""auto_signin_kit 异常体系: 由 cli 组合根统一转译为进程退出码。

按错误性质分类退出码,不要都笼统返回 1——这是为了让自动化调用方(CI 脚本、
其他程序)能通过退出码区分失败原因,不用非要解析错误文本。

下面只放了最基础的几类,按你项目实际需要的错误类型继续加子类,
每加一类给一个新的退出码,不要复用已有的。
"""


class AutoSignInKitError(Exception):
    """业务异常基类;退出码 1。"""

    exit_code = 1


class ConfigError(AutoSignInKitError):
    """配置缺失或非法;退出码 2。"""

    exit_code = 2


class CommandError(AutoSignInKitError):
    """命令参数/流程性错误;退出码 3。"""

    exit_code = 3


class AdapterError(AutoSignInKitError):
    """core/adapters 层失败的基类;本层错误应被域层翻译后上抛,退出码 4。"""

    exit_code = 4


# 示例: 项目自己的业务异常,退出码从 5 开始往下加
# (1/2/3/4 已分别归 AutoSignInKitError / ConfigError / CommandError / AdapterError)
# class SomeSpecificError(AutoSignInKitError):
#     """具体业务失败的某一类;退出码 5。"""
#     exit_code = 5
