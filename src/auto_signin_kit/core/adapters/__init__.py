"""适配基础设施: 桌面客户端发现/凭据解锁/子进程调用/平台探测。

本包是 OS 差异与本地系统交互的唯一落点——业务域(domains/)禁止出现
平台分支与直接子进程调用(架构门禁强制),相关能力都从这里导入。
"""

from __future__ import annotations

from auto_signin_kit.core.errors import AdapterError

__all__ = ["AdapterError"]
