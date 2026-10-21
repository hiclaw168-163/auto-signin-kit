# 配置规范

- **域 YAML 唯一读取入口**: `config/domains/<域>.yaml`,只经
  `core.config.load_domain_config_model("<域>", SchemaCls)`。禁止第二套域配置文件、
  禁止绕过该 API 直接 `yaml.safe_load`。
- 域配置必须定义 pydantic schema(`domains/<域>/schema.py`,继承
  `core.config.schema.DomainConfig`): 加载即校验,未知字段失败
  (`extra="forbid"`)。普通 `BaseModel` 不拒绝未知字段,YAML 手误会静默通过——
  所以 schema 忘继承 `DomainConfig` 是 bug,不是风格问题。
- **加载失败立即 `ConfigError` 退出(退出码 2),禁止静默回退默认值。**
  YAML 语法错误同样映射为 `ConfigError`,不泄漏 ParserError traceback。
- 配置目录解析(`core.config.resolve_config_dir`,惰性函数,**禁止**在模块顶层
  缓存成常量——测试重定向会失效,这个坑真实发生过):
  1. 环境变量 `AUTO_SIGNIN_KIT_CONFIG_DIR`(必须是已存在目录,否则 `ConfigError`)
  2. 包内 `auto_signin_kit/config`(wheel 分发布局)
  3. 仓库根 `config/`(editable 布局)

## 三类配置三种处置

| 类型 | 处置 |
|------|------|
| 机器无关非敏感(码表/约定/版式) | 直接入库 `config/domains/<域>.yaml` |
| 机器相关(本机路径/容器名/安装路径) | 入库 `config/examples/<域>.yaml.example` + gitignore 真实文件;example 头部写 `# 复制为 config/domains/<域>.yaml` 并填本机值 |
| 敏感(token/密码) | 同上,example 中值用占位符;运行时从环境变量或系统钥匙串读取,禁止明文入库 |

- example 模板是**契约的一部分**: 配置契约测试测 `config/examples/`,不依赖本机
  gitignore 的真实文件(否则换台机器测试就红)。

## 本机覆盖(`.local.yaml` 深合并)

机器相关的差异只有个别键时,不必整份走 example 方案,用**主 YAML 入库 + 本机覆盖**:

- 真实差异写同目录 `config/domains/<域>.local.yaml`(**必须 gitignore**,骨架生成的
  `.gitignore` 已含 `config/domains/*.local.yaml` 通配)。
- 读取时 `load_domain_config_model("<域>", Schema, merge_local=True)`:
  映射键递归合并、标量直接覆盖;local 文件不存在则原样通过(没有本机差异是常态)。
- 选型判据: **整份配置都机器相关 → example 方案;只有个别键机器相关 → `.local.yaml` 方案**。
- 敏感值两种方案都不例外: 占位符 / 环境变量 / 系统钥匙串,禁止明文入库。

## 优先级链

```
CLI 显式参数 > 环境变量 > 域 YAML 字段 > 代码兜底 / 交互询问
```

每一层都要能独立工作;`--yes/-y` 是"跳过交互确认"的唯一开关,
不要发明第二个非交互旗标。

## 配置 ↔ 域源码镜像对齐

- 域级: `config/domains/<域>.yaml` ↔ `src/auto_signin_kit/domains/<域>/`,
  同名对应,禁止发明第二套泛称命名。
- 找某域的配置时按源码目录名定位;配置路径与源码包路径永远镜像。
- 跨域共享的数据资产放 `config/data/`(或随包 `assets/`),命名见 STD-06 的判据。
