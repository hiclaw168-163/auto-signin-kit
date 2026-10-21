# 平台适配规范

「平台」专指 **OS 差异**(Windows / macOS / Linux)。其他外部系统的多实现
(GitLab/GitHub、不同渲染后端等)同样走 ports/adapters,判定标准见
仓库根 `AGENTS.md` 硬性规则 5,以及 `polydomain-cli` skill 的
`references/architecture-decisions.md`(仓库外文件)决策 1:
**这类依赖以后会不会真的出现第二种实现?** 不会就不建端口,不要提前设计。

## 判定(OS 差异)

出现以下任一,即 OS 平台差异,必须进 `core/adapters/` 适配层:

- 不同的可执行命令(explorer/open/xdg-open;winget/brew)
- PATH/profile 持久化机制差异(注册表+广播 vs shell rc)
- 进程管理(netstat+taskkill vs lsof+kill)
- 每用户可写目录定位(%LOCALAPPDATA% vs ~/Library/Logs vs XDG State)
  → 落在 `core/adapters/log_dir.py`,结果日志路径统一走 `core.logging.result_log_path`

## 步骤

1. `core/ports/<topic>.py` 定义 `Protocol` + 数据对象(入参 frozen dataclass、
   出参状态快照;「未运行」是合法状态不是错误)。
   **ports 只放形状,不依赖 adapters。**
2. `core/adapters/<topic>/` 下 windows/darwin/linux 各自实现;
   linux 无差异时继承 darwin 覆写差异方法。OS 命令差异**只出现在这一层**。
3. domains 的 service 函数收 `plat: Protocol | None = None`,缺省时惰性取工厂;
   cli 层显式注入。**禁止在 domains 里写 OS 分支。**
4. 适配器测试按 OS `skipif`,monkeypatch `subprocess.run` 断言调用序列
   (子进程统一走 core 封装的执行函数,方便统一加日志/超时/mock)。
5. **端口一致性测试跟第二种实现一起写**: 契约测试对所有实现跑同一组断言,
   防止某个平台悄悄漏方法。

## 门禁

`tests/test_architecture_gate.py` 扫描 `domains/**/*.py`:

- 命中 `os.name` / `sys.platform` / `platform.system(` / `os.startfile` → 失败
- 命中 `subprocess.run(` / `Popen(` / `call(` / `check_*` → 失败

纯子串扫描,注释里提到也会抓——这是有意的保守设计。
判断当前 OS 属于框架职责(组合根 / core 工厂),不属于业务命令。
