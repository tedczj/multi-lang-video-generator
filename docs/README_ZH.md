# 多语言视频生成项目文档

更新：2026-09-12。当前设计以 [plan_0.md](plan_0.md) 为基线：**单 Mac 主控、单实例轻量 MySQL、串行执行、独立模型 worker**。

第一期基础设施和媒体执行已落地，安装与运行见 [项目 README](../README.md)。真实 MySQL、重试恢复、媒体、故障、备份、YouTube 下载及空卷初始化均已有新证据，第一期已 PASS。详见 [第一期最终验收](verification/PHASE_1_ACCEPTANCE_ZH.md)。第二、三期仍为设计。

## 当前开发入口

| 文档 | 用途 |
|---|---|
| [完整开发计划](DEVELOPMENT_PLAN_ZH.md) | 需求编号、MySQL 表、文件与血缘、执行/重试/恢复、20 个节点、时间线、QA 和验收 |
| [第一期 kickoff](kickoffs/PHASE_1_KICKOFF_ZH.md) | 基础设施、MySQL、串行内核、下载/导入、规范化和真实媒体验证 |
| [第二期 kickoff](kickoffs/PHASE_2_KICKOFF_ZH.md) | 真实字幕/语音、归句、Codex 翻译、CosyVoice3 和双语候选成片 |
| [第三期 kickoff](kickoffs/PHASE_3_KICKOFF_ZH.md) | 硬字幕清理、样式恢复、OCR 对照、完整 QA、长片、备份和发布 |
| [实现参考与核查记录](IMPLEMENTATION_REFERENCES_ZH.md) | 固定源码阅读 commit、具体文件/函数、复用边界与新开发内容 |
| [本次文档核查](verification/DOCS_UPDATE_CHECK_ZH.md) | 文档链接、编号、默认值与来源核查，区分本次检查和历史运行结果 |

实施先读完整计划，再进入所属期 kickoff。新方案与历史材料有冲突时，以完整计划及原始大纲为准。

固定业务选择：Codex CLI `gpt-5.6-terra` / `medium`，每批最多 8 个完整语句且源文不超过 6,000 字符；官方 `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`；英文后 1 秒播放中文，中文后至少 1 秒；优先使用自然空档，只补必要定格，英文字幕上、中文下。

## 文档与源码来源

| 材料 | 对应文件 / 包内目录 | 使用边界 |
|---|---|---|
| 当前大纲 | [plan_0.md](plan_0.md) | MySQL、串行和三期交付的设计依据 |
| 旧 Bivideo 设计 | [DEVELOPMENT_DESIGN_V2_ZH.md](DEVELOPMENT_DESIGN_V2_ZH.md)、[CODE_REFERENCES_ZH.md](CODE_REFERENCES_ZH.md)、[REAL_TEST_MATRIX_ZH.md](REAL_TEST_MATRIX_ZH.md)、[TEST_REPORT_ZH.md](TEST_REPORT_ZH.md) | 来自旧 Bivideo 包；SQLite/旧 CLI/旧测试不作为当前验收 |
| VidFlow 参考规格 | [DATA_CONTRACTS_ZH.md](DATA_CONTRACTS_ZH.md)、[TEST_PLAN_ZH.md](TEST_PLAN_ZH.md)、[UPSTREAM_REFERENCES_ZH.md](UPSTREAM_REFERENCES_ZH.md)、[TEST_RESULTS_ZH.md](TEST_RESULTS_ZH.md) | 业务契约和算法可参考；原执行/存储与测试声明属于 VidFlow 包 |
| 历史讨论 | [draft.md](draft.md) | 两轮设计讨论留档；其中 sandbox 下载地址是原会话路径 |
| 初始原型 | [bilingual_video_research_and_prototype.zip](../sources/bilingual_video_research_and_prototype.zip)，根 `bilingual_video_research/` | 早期时间线和样片，不作为当前算法基线 |
| Bivideo 骨架 | [bivideo_design_and_reference_v2.zip](../sources/bivideo_design_and_reference_v2.zip)，根 `video_pipeline_v2/` | 包内 `src/bivideo/`；旧运行说明在包内 README |
| VidFlow 骨架 | [video_interleave_v2_design_and_skeleton.zip](../sources/video_interleave_v2_design_and_skeleton.zip)，根 `video_interleave_v2/` | 包内 `vidflow/`；参考执行/血缘/媒体算法，不照搬数据库和并发设计 |

`bivideo`、`vidflow` 和当前 `mlvideo` 的 CLI 与存储结构不同，不能交叉使用命令、测试数字或结果目录。

## 查看或运行历史参考包

先从当前项目根目录解压到独立的新目录，再阅读包内 README。下面只有解压命令适用于当前工作区：

```bash
REFERENCE_DIR="$(mktemp -d /tmp/mlvideo-reference.XXXXXX)"
unzip -q sources/video_interleave_v2_design_and_skeleton.zip -d "$REFERENCE_DIR"
cd "$REFERENCE_DIR/video_interleave_v2"
```

包内需 Python 3.11+、JSON Schema 依赖、pytest 和 FFmpeg/ffprobe。按包内 README 安装并运行 `python -m vidflow.cli doctor`；演示入口是 `python -m vidflow.demo --out <新的空目录>`。这些命令验证历史 VidFlow，不能证明 MySQL 新项目已经实现。

历史报告分别记录 Bivideo **49 passed / 2 skipped** 和 VidFlow **48 passed / 2 skipped**。它们来自各包的 Linux 验证；本次文档更新未重跑两套测试，也未执行 MySQL、真实下载或模型闭环。VidFlow 提示音演示为 12 秒源片到 17 秒输出，和 Bivideo 的短/长空档演示不是同一个用例。

本次核对了源码包与文档的对应关系、上游固定源码文件和本机 Codex CLI 帮助。详情见 [文档核查记录](verification/DOCS_UPDATE_CHECK_ZH.md)。
