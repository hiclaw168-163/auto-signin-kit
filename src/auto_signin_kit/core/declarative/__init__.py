"""声明式签到底座: 「一份 YAML 接入一个应用」的通用机制(工厂 + 策略)。

web_signin(网页版签)与 client_signin(客户端签到)两个引擎域的共享层,
按 **凭据来源** 分类(见 constants 模块注释):

- ``schema.py``    应用声明模型(``config/apps/<app_code>.yaml`` 的校验)
- ``apps.py``      声明加载器(扫描/严格与宽松两档/原子回写)
- ``authkit.py``   凭据适配器注册表(策略): plain/ss_aes/chromium_cookies
                   按 ``auth.kind`` 分发,新凭据形态加适配器即可
- ``executor.py``  HTTP 请求执行器(status 查询 → 幂等 claim → 确认)
- ``service.py``   引擎服务层(运行期域对象 + ``--app`` 统一入口,
                   两个引擎域各以 ``kind`` 钉住自己的接管范围)
- ``ss_aes.py``    Trae/iCube 桌面端 SS_AES 加密信封适配器
- ``chromium_cookies.py`` 客户端 Chromium 数据目录解 Cookie 适配器

core 层不感知注册表与 UI;引擎域(domains/web_signin、domains/client_signin)
是这里之上的一层薄封装 + 调度声明。
"""
