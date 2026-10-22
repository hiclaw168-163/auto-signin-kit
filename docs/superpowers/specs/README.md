# Auto_signin_kit as-built 文档索引(SPEC-)

本目录是**as-built 实现文档**(回答"代码实际是什么样"),命名与约定见
`docs/standards/STD-06-脚本与资产规范.md` 第二节。与规范文档(回答"应该是什么样")
相对:代码变了 SPEC 必须跟着变,见 SPEC-01 §8 的同步义务表。

**整体架构只有一个 SPEC**(SPEC-01);每个组件(公共层、各域)各一份。

| 文档 | 主题 | 状态 |
|------|------|------|
| [SPEC-01-架构总览.md](SPEC-01-架构总览.md) | 分层 / 组合根 / 依赖规则 / 数据流 / 错误与退出码 | 已实现 |
| [SPEC-02-core层公共契约.md](SPEC-02-core层公共契约.md) | httpclient / report emit / logging / config / errors / prompt / orchestration / adapters | 已实现 |
| [SPEC-03-workbuddy域.md](SPEC-03-workbuddy域.md) | WorkBuddy 签到 + 成长中心(13 文件) | 已实现 |
| [SPEC-04-qoder域.md](SPEC-04-qoder域.md) | Qoder CN Credits 领取(7 文件) | 已实现 |
| [SPEC-05-trae域.md](SPEC-05-trae域.md) | Trae CN 签到(SS_AES 信封,6 文件) | 已实现 |
| [SPEC-06-schedule域.md](SPEC-06-schedule域.md) | 定时调度: 注册/注销/查看每日自动任务(三平台) | 已实现 |
| [SPEC-07-history域.md](SPEC-07-history域.md) | 签到台账: SQLite 存储 + history 查询/回填 | 已实现 |

关键架构决策单独沉淀在 `docs/adr/`(见根目录该目录的各 ADR):

| ADR | 决策 |
|-----|------|
| ADR-0001 | 域注册用 entry-points,cli.py 永远做纯组合根 |
| ADR-0002 | OS 差异收口 core/adapters;端口只在第二种实现出现时才建 |
| ADR-0003 | 请求层带时间预算,预算优先于"再多试一次" |

逆向取证类文档(端点/信封逆向过程与证据链)在 `docs/notes/`,索引见
[`docs/notes/README.md`](../../notes/README.md);`NOTE-NN` 与本表域 SPEC 一一对应。
