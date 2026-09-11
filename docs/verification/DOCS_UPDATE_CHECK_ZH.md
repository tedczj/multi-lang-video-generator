# 2026-09-11 文档更新核查

本报告仅记录文档检查。当前实施依据为 [plan_0.md](../plan_0.md)，交付入口为 [README_ZH.md](../README_ZH.md)。**没有执行新项目的 MySQL、媒体、网络下载或模型验收。**

## 本次交付

- [完整开发计划](../DEVELOPMENT_PLAN_ZH.md)：6 类需求、20 个节点、26 个测试编号、11 组源码参考，覆盖 MySQL、单主控串行、血缘、真实重试和三期交付。
- [第一期](../kickoffs/PHASE_1_KICKOFF_ZH.md)、[第二期](../kickoffs/PHASE_2_KICKOFF_ZH.md)、[第三期 kickoff](../kickoffs/PHASE_3_KICKOFF_ZH.md)：前置条件、顺序任务、接口、参考、待实现验收命令和完成标准。
- [实现参考](../IMPLEMENTATION_REFERENCES_ZH.md)：本次解析/取回 9 个上游仓库的 14 份固定源码文件，记录完整 commit、文件 SHA-512 与符号定位。
- README 更新为当前入口；8 份参考包文档新增历史说明且正文逐份与 zip 原文一致；draft 保留历史会话，plan_0 保留大纲并添加交付链接。

## 检查范围与结果

| 检查 | 结果 / 边界 |
|---|---|
| 本地 Markdown 链接与章节锚点 | 当前文档导航通过；程序检查明细见下方 JSON |
| 历史 sandbox 链接 | draft 中保留 14 个旧会话链接，页首明确当前不可用；现有来源包另在 README 提供，不计作当前导航 |
| 示例命令 | 7 个 bash 代码块通过 `bash -n`，仅检查语法；项目尚未实现，未执行这些业务命令 |
| 需求、节点与测试覆盖 | REQ-01–06、N01–20、26 个测试编号、11 个参考编号齐全；主计划中的测试编号均出现在 kickoff |
| 默认值 | MySQL 8.4 / 3307 / 128 MiB / 10 连接 / 768 MiB；Codex 指定模型与 medium / 8 段 / 6,000 字符；指定 CosyVoice3 / pre=post=1 秒一致 |
| 历史正文 | 8 份参考文档只插入顶部说明，正文与对应 zip 一致；旧 README 原文仍保留于 Bivideo 压缩包 |
| 核查源码身份 | 9 仓库、14 文件的 SHA-512 与本次取得内容一致；不等于部署环境已经锁定或验证 |
| CLI | 本机读取 `codex --version`、`codex exec --help`，版本 0.153.4；没有执行推理或检查账户可用性 |
| 工作区 | 当前无 `.git`、正式源代码、Compose 或新测试入口；未创建或修改业务源码 |

机器可读明细：[doc-check-results.json](doc-check-results.json)。源码核查明细：[upstream-source-checks.json](upstream-source-checks.json)。本机 CLI 证据：[版本](codex-version.txt)、[exec 帮助](codex-exec-help.txt)。

本次按源码和官方资料核对了 MySQL 启动配置、Codex 结构化输出、CosyVoice3 调用及继承关系。源文件核查不是模型/许可证/目标设备兼容性验收；这些阶段任务保持待执行状态。报告不把旧包的 49/48 项通过计入新项目。

## 原始源码包身份

以下为本次读取的 ZIP 文件 SHA-256；本次未修改 `sources/`。

| 文件 | SHA-256 |
|---|---|
| `bilingual_video_research_and_prototype.zip` | `463c03775e15d0ce8e9127f4ec6679c57c045333f044a51c7252c8feaeb1792e` |
| `bivideo_design_and_reference_v2.zip` | `d004ef554d1132acbf6ce6b9eac13fa7c4a0f99447443eea4bfd8fd6387ce0c8` |
| `video_interleave_v2_design_and_skeleton.zip` | `2ee0afb4297d2d2a2136238d387f4e21b88c34e7d8ad506657ce3abc297e9e0d` |

执行验证时必须另生成实际环境、素材、过程日志和结果。项目命令能通过 shell 语法检查、源码符号存在，均不表示相应业务已经运行成功。
