# 第二期候选链路：实现、整体验证与问题记录

> 后续用户已提供实际 YouTube 原片并确认画面中的英文硬字幕。新的真实素材预览、OCR 参数修复与原字幕像素保留验证见 [指定原片预览报告](YOUTUBE_Ye33eY4UNtY_PREVIEW_ZH.md)。下文保留此前控制样本的验证边界。

日期：2026-09-12。本轮在已有未提交的 N11/N12 代码基础上，补齐 N05–N12、N16–N19 的候选执行路径；未提交 Git、未执行第三期正式发布。第二期正式验收仍为 **INCOMPLETE / REVIEW**，不能将控制样本跑通解释为真实视频集质量通过。

## 实现范围

- 字幕：N05 清点原始软字幕轨并留存截图；N06 支持单一软字幕轨解析、时间基转换及 PaddleOCR 逐帧调用。原图、请求、响应、耗时和文字框保留；ROI 中的文字仍是字幕候选，硬字幕有无保持 unknown，未实现人工真值比较器。
- 语音：N07 真实 Whisper small 英语转录/词时间与独立 Silero VAD，保存模型权重 SHA-512、原始 ASR 和 VAD。未确认人声覆盖时保护整段 PCM，N08 归为完整受保护语句，防止插入中文时覆盖漏识别人声。支持绑定规范化 PCM 哈希的人工语句/说话者标注；标注不可遗漏 ASR/VAD 检出的人声。
- 翻译：N09 每批独立 execution，固定本机 Codex `gpt-5.6-terra / medium`，最多 8 个完整 unit、6,000 Unicode 字符。保存 prompt/schema 哈希、JSONL、原始响应、回执，拒绝缺失/新增/重复 ID 和空译文。resolved backend model 未由 CLI 回报，保持 null+原因。
- 音频：N10 从原 PCM 按明确区间/转录提取参考音；N11 沿用 CosyVoice3 zero-shot。N12 新增 `audio_qa_asr`，在完整 PCM 转换及削波/前导检测外实际调用固定 Whisper 中文 ASR，保存完整原始识别与辅助 CER。ASR 不是内容真值，不自动裁剪、不因识别成功宣称音色/尾音通过。
- 时间线/成片：N16 绑定译文、raw/dub clip、speaker、unit ID 和实测音频长度；N17 检查固定字体字形覆盖、英文上中文下、像素边界和下半屏容量；N18 使用完整真实 WAV 并按输出时间叠加字幕，输出 FFV1/PCM 母版和 MP4 预览，核对解码及帧/采样数。
- 复核：N19 要求 QA 来自同一次 render、固定布局和全部原始译音，支持绑定 render artifact/hash 的人工决定。缺少人工决定保持 REVIEW；不允许错误绑定、缺项或矛盾决定产生 PASS。
- 主控：`candidate` 串行运行，逐批/逐句保留版本和血缘；修复 retry 只取每个端口第一个输入的问题，重试保留完整有序输入列表与原模型声明。第一期提示音策略保持原含义，第二期使用 `dub_gap_first` / `dub_ffmpeg`。

部署与所有策略入口见 [workers/README.md](../../workers/README.md)。主控新增 Pillow 12.1.1 和 fonttools 4.61.1；模型仍在独立 Python 环境执行，数据库保持 MySQL/单写入者。

## 本轮素材与运行边界

固定输入：[phase2_inputs_001/manifest.json](../../evidence/phase2_inputs_001/manifest.json)。声音为已有 LibriSpeech 1272 英语片段；视频背景为 FFmpeg 生成的 1280×720 纯色画面。这是**真实音频控制样本**，不是正式真实视频素材，也不替代多人素材或真实烧字 20 帧。

本轮使用 `config/local-phase2-001.json`，沿用第一期隔离 MySQL 数据库和 `evidence/phase1_data_007` 资产根目录。目录名保留历史命名，第二期通过新的 asset/run/execution 区分；没有重建数据库或覆盖旧执行。所有模型调用在同一个主控锁内串行执行。

- `phase2_run_001`：早期联调已生成成片，但 N19 旧请求缺少新增 `human_decision` 字段导致失败。已修复兼容处理，原失败 request/日志和成片保留，不修改其状态为成功。
- `phase2_run_002`：两次完整候选执行和真实 N09/N11 retry 完成，执行错误为 0。该次主控启动时尚未纳入新增 `audio_qa_asr`；不能把它作为最终内容 ASR 的验证。
- 最终代码执行与可直接查看的交付文件，在本报告下方列出。

第二轮离线检查：[offline-audit-002](../../evidence/phase2_run_002/offline-audit-002/)。两个候选均验证 artifact 哈希、完整源 PCM、完整译音插入以及母版解复用 PCM 一致。各为 282,240 个源采样帧，668,160 个成片采样帧，中文从 6.88 秒开始，pre 间隔为 48,000 帧。该检查证明技术保真，不能证明翻译和听感质量。

## 未解决项与后续复现

| ID | 问题 / 影响 | 本轮处理和所缺条件 |
|---|---|---|
| P2-01 | 缺少单人/多人真实视频及人工语句/说话者/字幕框真值；无法完成 OCR 20 帧、真实 CER/WER/IoU 和语音边界验收 | 已询问指定素材路径；目前仅使用真实音频控制样本。正式样本须单独冻结并重跑，不能复用本轮控制画面充数 |
| P2-02 | 没有可验证的自动 diarization/独立 forced alignment 部署；自动逐句安全切分和多人克隆未完成实测 | [实际环境探测](../../evidence/phase2_run_003/deployment-probe.json) 中 Whisper 可用，pyannote/whisperx 不可用。已实现真实 VAD/Whisper 词时间及人工标注入口；未确认时整段保护。长素材可能触发 6,000 字符或字幕容量 REVIEW，不能据此宣称具备完整自动多人能力 |
| P2-03 | 既有 CosyVoice 前导细小声音尚未定位并修复；阈值能量检测无法判断首字起点 | 本轮再次对旧问题音频实测，持续能量起点仍为 2.60 秒，overall=REVIEW，trimmed_samples=0；[回归证据](../../evidence/phase2_regression_001/leading-original/qa.json)。原音保留，无固定时长盲裁。需要用户试听/人工首字位置及更多真实参考音才能验证处理方案 |
| P2-04 | 中文 ASR 是辅助测量，不能替代音色、自然度、专名发音和尾音的人工判断 | 最终轮实际出现“奎尔特/中产”→“QQ特/中坦”的 ASR 差异，辅助 CER=12.5%，不能据此单独断言发音错误或正确。提供原始 ASR、译音和预览；N19 未收到真实人工决定，保持 REVIEW |
| P2-05 | OCR 服务未回报完整部署/权重身份，CosyVoice 全量权重哈希未在 worker 中测量；字体仅固定本机基准 | 不伪造 revision/哈希，不声称其他部署速度或质量已验证；PaddleOCR 保留实际请求与服务声明，Whisper/Silero/字体有实际 SHA-512 |
| P2-07 | 补测时原 PaddleOCR 服务退出，58081 连接拒绝；退出原因未知 | 旧日志无 shutdown/异常栈，未推断为模型或主控原因。用同一 YAML/模型恢复本机服务，固定输入重试 N06 成功；[新服务启动与日志](../../evidence/phase2_ocr_service_001/launch.json)。服务长时间稳定性仍待验证 |
| P2-06 | 正式第二期验收器尚未具备真实视频真值/人工复核汇总后的全项 PASS 判定 | 当前 `--phase 2` 是 fail-closed 验证入口：自动运行两轮、真实 retry、记录问题并返回 INCOMPLETE。已有单元及离线断言不替代缺失的正式验收矩阵 |

上述待完成项是本轮已知边界，不标记 VERIFIED/PASS；N20 正式发布仍在第三期。

## 复跑

输出目录必须是新的：

```bash
.venv/bin/python scripts/verify_phase.py --phase 2 \
  --config config/local-phase2-001.json \
  --fixtures evidence/phase2_inputs_001/manifest.json \
  --out evidence/phase2_run_NEW

.venv/bin/python scripts/audit_phase2_run.py \
  --config config/local-phase2-001.json \
  --runs evidence/phase2_run_NEW/runs.json \
  --out evidence/phase2_run_NEW/offline-audit
```

验收入口返回 1 代表第二期尚未整体通过；具体区分已执行、失败和待人工项目应阅读 `case-results.json`、`runs.json`、各 execution 的 request/result/日志，不能只看退出码。`tests/fixtures/manifest.json` 是第一期合成素材清单，传给第二期会生成 INCOMPLETE 说明，不能绕过素材要求。

源码中 Codex CLI 参数以本机 `codex exec --help` 和真实调用为准；接口说明参考 [OpenAI 非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。Whisper/Silero 用法分别核对 [Whisper 官方源码](https://github.com/openai/whisper) 和 [Silero VAD 官方源码](https://github.com/snakers4/silero-vad)；本机实际部署哈希记录在执行请求和模型回执中。

## 最终代码执行结果

最终 [phase2_run_003/case-results.json](../../evidence/phase2_run_003/case-results.json)：两次完整候选运行、0 个执行错误；成功翻译批次和 TTS 句子均真实 retry。总体仍为 INCOMPLETE / REVIEW，原因是上表的正式素材与质量复核缺项。[源码一致性检查](../../evidence/phase2_run_003/source-consistency.json) 确认该两轮启动前与完成后的生产源码、schema、脚本和测试树哈希一致，没有用后续代码修改冒充已执行版本。

| 检查 | 结果与证据 |
|---|---|
| 单元测试 | 35 passed：批次 8/9、6,000/6,001 Unicode 边界、错误 ID、危险归句、遗漏 VAD 人声、完整 retry 输入、错误人工决定等 |
| 新媒体独立 oracle | 2 passed：完整 WAV 插入、原 PCM 完整、定格期间按输出帧更新覆盖层、错误采样数拒绝 |
| 第一期开关/媒体回归 | 13 passed：规范化、分数帧率、多个切点、长片尾和音频提前等 |
| 真实 MySQL 与旧配方 | 2 passed：原重试/血缘及第一期完整媒体配方；[JUnit](../../evidence/phase2_regression_001/db-junit.xml) |
| 回归合计 | 52 个测试通过，范围与命令见 [validation.json](../../evidence/phase2_regression_001/validation.json)；不含正式人工质量验收 |
| 最终真实模型 | 每轮真实调用 OCR、Whisper 英语、Silero VAD、Codex、CosyVoice3、Whisper 中文；无 fixture 音频替代 |
| 离线成片核验 | 两轮 artifact 哈希、原声+完整译音 PCM、母版解复用 PCM 均 PASS；[第一轮](../../evidence/phase2_run_003/offline-audit/run-1/audit.json)、[第二轮](../../evidence/phase2_run_003/offline-audit/run-2/audit.json) |
| 字幕像素检查 | 助手查看两轮实际成片截图，英上中下、无可见越界；这不是用户人工批准，仍保留 REVIEW |
| 环境/静态 | doctor ok；pip check 无依赖冲突；新增源码 Ruff 和 git diff --check 通过 |

执行身份：

- 第一轮：`run_69e3ab151a5b41d6a35484a173577f79`，成片 13.92 秒。
- 第二轮：`run_2c22a9ed3d1d48c590b6518c964093cd`，成片 15.00 秒；译文为“欢迎他带来的福音”，与第一轮“欢迎他的福音”不同，两个真实结果均保留。
- 翻译 retry：`exec_d8bc3268f8d0409da507be4bd6adbe7c`，version 7，原输入与原模型声明一致。
- TTS retry：`exec_44f9f63af6ee4219ae23dfb6d41dfe5f`，version 7，原输入与原模型声明一致。

两轮完整 source PCM 均为 282,240 帧。中文从 6.88 秒开始；第一轮中文 289,920 帧，第二轮 341,760 帧。实际保留 pre=1 秒；中文后分别还有 1.00 和 1.00 秒，母版的帧/采样数一致。参考片段中真实讲话结束可能早于保守受保护区间的结束，本轮未将该保守区间冒充人工核定的语音边界。

两轮中文辅助 CER 分别 12.50%、11.54%；都存在专名的 ASR 差异，第二轮识别为“QQ先生”。这些是需试听的具体问题，不宣称已通过内容准确性检查。

便于试听的副本：

- [候选 1 MP4](../../evidence/phase2_run_003/delivery/candidate-1-preview.mp4) · [中文音频](../../evidence/phase2_run_003/delivery/candidate-1-chinese.wav) · [内容 ASR](../../evidence/phase2_run_003/delivery/candidate-1-content_asr.json)
- [候选 2 MP4](../../evidence/phase2_run_003/delivery/candidate-2-preview.mp4) · [中文音频](../../evidence/phase2_run_003/delivery/candidate-2-chinese.wav) · [内容 ASR](../../evidence/phase2_run_003/delivery/candidate-2-content_asr.json)

[副本 manifest](../../evidence/phase2_run_003/delivery/manifest.json) 保存原 artifact ID 和 SHA-512，复制后核对一致。全部文件均为候选复核材料，没有发布或自动通过人工复核。


## OCR 正例补测与服务恢复

最终候选两轮使用无字控制背景，另对已有 `sources/result.mp4` 的有字幕控制片执行 N05/N06 业务流程。第一次调用遇到本机 58081 连接拒绝，保留失败 execution `exec_11def7a493984610bb5f8fa5e8328051`；该故障发生在两次最终候选运行及其模型重试完成之后，不改写它们的成功执行记录。

重启 PaddleX 原始 `/ocr` 服务后，使用主控 retry 固定原 N06 输入重新执行，成功得到 12 条带矩形框记录，包含 `First example.` / `第一个示例。` 和 `Second example.` / `第二个示例。`；ROI 排除了左上时间码。[当前正例结果](../../evidence/phase2_ocr_business_002/summary.json) 保存全部 cue、原失败/新 retry 身份与验证脚本哈希。它验证了业务 worker 的正例文字/框解析与输入冻结，不代表真实视频框 IoU 或完整字幕时间边界通过。
