# GUIDE-03 · 排错手册

> 按报错对号入座。每条都写清「症状 → 原因 → 处理」,带「已修」的是踩过并已
> 加回归测试的坑,不要再改回旧写法。

## 命令根本没出现

**症状**: `auto-signin-kit --help` 里没有新域;或 `auto-signin-kit <域>` 报未知命令。

**原因**: 域注册表写在包的 dist-info 里,`--editable` 只对源码生效,对
`pyproject.toml` 的改动不生效——你改了 entry_points 但没重装。

**处理**:

```bash
uv sync && uv tool install --editable . --force
```

---

## 注册定时任务报「用户名或密码不正确」

**症状**: `schedule install` 失败,schtasks 返回这句(而且早期版本这行还是乱码)。

**原因**: `<LogonType>InteractiveTokenOrPassword</LogonType>` 要求 schtasks 存一份
凭据,非交互注册时拿不到,直接失败。

**处理**: **已修**——必须用 `InteractiveToken`(有回归测试守着)。代价是任务只在
用户已登录的会话里运行,这对签到场景是正合适的: 用户没登录时本来也读不到桌面
端的登录态,跑了也没用。**不要"修好"它。**

---

## schtasks 报「XML 格式错误」

**原因**: 任务计划程序的 XML 文件必须是 **UTF-16**,写成 UTF-8 就会被拒。

**处理**: **已修**——`core/adapters/scheduler/windows.py` 写临时文件时按 UTF-16 编码。

---

## Windows 上 schtasks 的错误信息是一坨乱码

**症状**: 报错里全是 `?????` 或替换字符,看不出真实原因。

**原因**: schtasks 输出的是控制台 ANSI 编码(中文系统即 GBK),按 UTF-8 解就烂了。
这很坑——连"用户名或密码不正确"这种关键提示都读不出来,排查方向直接被带偏。

**处理**: **已修**——`core/adapters/scheduler/common.py` 的 `_decode()` 先试 UTF-8,
出现替换字符就退回系统编码。

---

## Git Bash 里手工跑 schtasks 说任务不存在

**原因**: MSYS 把 `/TN` 后面的任务名当路径做了转换。

**处理**: 手工调试时加前缀 `MSYS_NO_PATHCONV=1 schtasks /Query /TN "<任务名>"`。
这只影响你手工验证,工具本身走 subprocess 不受影响。

---

## 退出码 2,结果 `NO_AUTH`

**症状**: 命令返回 `{"result": "NO_AUTH", ...}` 并带 `looked_in` 数组。

**原因**: 没找到可用凭据。`looked_in` 列出了所有查过的路径,照着看就知道去哪找。

**处理**: 先在对应**桌面客户端里登录一次**。仍不行就跑 `doctor`(离线检查凭据格式
与解密运行时能力);再不行多半是客户端升级改了凭据存放格式或加密方式——
workbuddy 的 `sym-v1` 加密凭据要调客户端运行时 exe 解密,客户端大版本升级后
需要重新对齐 `helper_js.py`。

---

## 配置改了不生效 / 报配置错误

- 改的是 `config/domains/<域>.yaml` 却没生效 → 检查有没有同名的 `.local.yaml`
  在覆盖(本机覆盖优先级更高,且**不入库**)。
- 报 `ConfigError`、退出码 2 → 配置模型是 `extra="forbid"`,**写了 schema 里没有的
  键会直接报错**,不是被忽略。对照该域 `schema.py` 改键名。

---

## 定时任务注册成功但从不执行 / 日志写到一半就断

按顺序查:

1. **执行时限小于脚本自己的时间预算** → 任务在收尾前被平台强杀。时限(分钟)必须
   大于该域的时间预算(秒)。有测试 `test_execution_limit_covers_domain_budget` 守着,
   改小了会在测试期就红。
2. **Windows 上用户没登录** → 见上面 `InteractiveToken`,这是设计如此。
3. **`auto-signin-kit` 不在 PATH 或路径变了** → 注册时记的是解析到的绝对路径,
   重装/换 venv 后要重新 `schedule install` 覆盖。

---

## 门禁红了

| 报错 | 原因 | 处理 |
|------|------|------|
| `test_source_file_line_limit` | 单文件超 410 行 | 拆文件,不要调高上限 |
| `test_service_modules_do_not_import_typer` | `service.py` 里 import 了 typer | 交互确认走 `core.prompt` 注入,别在业务层碰 CLI |
| `test_domain_cli_files_do_not_import_business_modules` | `cli.py` 直接 import 了 `net`/`api`/`auth` 等实现模块 | 业务留在 `service.py`,cli 只调它 |
| `lint-imports` 报 `domains 之间彼此独立` | 域之间互相 import 了 | 抽公共部分到 `core/`,不要跨域 import |
| `lint-imports` 报 `ports 不依赖 adapters` | `core/ports/` 反向依赖了具体实现 | 端口只放 Protocol 与数据对象 |
| 测试里写死的"已注册 4/4"变红 | 任务数变了,分母写死了 | 用 `len(TASKS)` 之类动态取值 |

---

## 测试全绿,但真跑还是不对

这不是意外,是本项目的已知教训: 定时调度那块是测试全绿、真机注册才暴露出
`LogonType` 与编码两个问题。所以流程上硬性要求**改完手动真实执行一次命令**,
不要只看测试绿。能在真机上验的一定在真机上验(注册任务、查下次运行时间),
不能的(launchd/cron)至少跑 `schedule render` 看生成的配置对不对。
