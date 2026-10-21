# 逆向取证文档索引(NOTE-)

本目录存放**逆向取证类文档**:桌面客户端的端点面、凭据信封协议、上游项目分析
等"结论是怎么得出来的"证据链。命名与约定见
`docs/standards/STD-06-脚本与资产规范.md` 第二节。

与 SPEC 的分工: SPEC 回答"**代码现在是什么样**",NOTE 回答"**为什么知道该这么写**"。
代码重构不要求回头改 NOTE(它记录的是取证过程);但**端点/信封协议变了,取证结论
失效时**必须当次更新对应 NOTE。

编号按域顺序接续(不跳号),每份头部标注对应的 SPEC:

| 文档 | 取证对象 | 对应 SPEC |
|------|----------|-----------|
| [NOTE-01-workbuddy上游项目分析.md](NOTE-01-workbuddy上游项目分析.md) | 上游 `88lin/workbuddy-auto-signin`(MIT):WorkBuddy 签到 + 成长中心 13 个接口契约、可靠性设计、调度方案 | [SPEC-03-workbuddy域](../superpowers/specs/SPEC-03-workbuddy域.md) |
| [NOTE-02-qoder积分面分析.md](NOTE-02-qoder积分面分析.md) | Qoder CN 客户端 asar/运行时/webview 缓存全量扫描:证明积分入口只有 campaigns 一条链路 | [SPEC-04-qoder域](../superpowers/specs/SPEC-04-qoder域.md) |
| [NOTE-03-trae签到接口逆向.md](NOTE-03-trae签到接口逆向.md) | Trae CN 签到两条 POST + SS_AES 凭据信封的六步证据链 | [SPEC-05-trae域](../superpowers/specs/SPEC-05-trae域.md) |

新增取证文档接续 `NOTE-` 编号并同步本表。取证方法可跨域复用时(NOTE-02 末尾就
写了给 trae 域复用的方法),在文档末尾显式写出,不要让人重新踩一遍。
