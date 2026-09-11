# 第二期 kickoff：真实翻译与克隆闭环

状态：待开发。基线：[完整开发计划](../DEVELOPMENT_PLAN_ZH.md)，覆盖 REQ-02、REQ-04–06，并沿用 REQ-03 的不可变血缘。项目命令为待交付接口。

## 1. 前置条件与完成目标

[第一期](PHASE_1_KICKOFF_ZH.md) 的真实 MySQL、媒体、重试与恢复验收已通过，代码身份和接口已固定。模型 worker 使用独立环境，仍由单个 Mac 主控顺序调用。

准备无硬字幕的真实英语素材、人工语句/说话者标注、可用 Codex 登录环境、指定 CosyVoice3 模型及参考语音。软字幕或 ASR 提供文本；另备烧字素材验证 OCR 节点。字体须可用且有明确来源；记录文件哈希。

本期完成真实英文原声后接中文的候选成片，字幕英上中下、逐句来源明确、模型调用与音频真实可复核。硬字幕修复及正式发布交给第三期。

## 2. 开发顺序与接口

| 顺序 | 节点 / 任务 | 输入 → 输出和关键规则 | 验证 ID |
|---|---|---|---|
| 1 | 模型环境探测 | 每类 worker 的真实代码、依赖、模型 revision、设备与冒烟报告；不改第一期存储方式 | T-CONTRACT |
| 2 | N05–N06 字幕清点与提取 | CanonicalMedia → SubtitleInventory/CaptionTrack；保留字框、时间、截图，真实 PaddleOCR | T-OCR |
| 3 | N07 语音分析 | 原声 → SpeechTrack；原始人声全覆盖，不能只根据字幕或静音检测切音 | T-SPEECH |
| 4 | N08 归句 | CaptionTrack+SpeechTrack → UtteranceSet；多对多关联与最后安全整帧切点 | T-UTTERANCE |
| 5 | N09 合批翻译 | UtteranceSet → TranslationBatch/TranslationSet；Codex，固定 ID | T-TRANSLATE |
| 6 | N10–N11 参考音与克隆 | speaker 参考音+译文 → RawDubClip；官方 CosyVoice3，逐句完整音频 | T-TTS |
| 7 | N12 音频检查 | RawDubClip → DubClip+QA；解码/内容/采样/削波/截尾 | T-AUDIO |
| 8 | N16–N18 时间线、字幕、合成 | 实测译音长度规划；基准字体、英文上中文下；输出母版和预览 | T-TIMELINE、T-LAYOUT、T-RENDER |
| 9 | N19 基础质量与人工复核 | 固定成片 → QAReport+ReviewDecision；真实语义/听感/布局证据 | T-QA |
| 10 | 真实模型 retry 和全链路串行重跑 | 固定原输入，真实再次调用；每批/每句版本完整 | T-RETRY、T-LINEAGE |

所有策略遵循 [主计划 §5](../DEVELOPMENT_PLAN_ZH.md#5-插件标准结果与代码身份) 的 `--request/--result` 协议；worker 不直接写数据库。按 source artifact 选择文本，跨 OCR 策略的 cue ID 不自动对应。

## 3. 固定参数和异常处理

| 项目 | 默认值 / 规则 |
|---|---|
| 翻译 | 本机 Codex CLI；`gpt-5.6-terra`；`medium` |
| 批次 | 连续完整 unit；最多 8 段且源文合计 ≤6,000 个 Unicode 字符 |
| 超长单句 | 不截断，重新归句或 REVIEW；上下文及输出预算另记 |
| 返回校验 | unit_id 集合与数量完全一致；拒绝缺失/新增/重复，记录最终 schema 结果与 JSONL |
| TTS | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`；逐句、原速、`stream=False`，读取全部返回块 |
| 音频工作格式 | 48 kHz、PCM16、双声道；保留模型原始音频与采样率 |
| 时间线 | 英文后 1 秒开始中文，中文后至少 1 秒；优先原片空档 |
| 字幕 | 英文上中文下，字体文件固定；无原字体时使用有记录的基准字体 |

执行参数和证据详情见 [主计划 §7](../DEVELOPMENT_PLAN_ZH.md#7-翻译与-cosyvoice3-接入)。Codex CLI 参数可用不等于账户模型已可用；本期必须真实调用。模型无法加载或运行环境不支持时记录实际错误，禁止用提示音、fixture 或其他模型替代验收。

按 speaker 固定参考音及准确转录，记录从源 PCM 提取的区间和处理。声音效果、跨语言内容与截尾通过自动检测和人工抽听共同判断；真实模型未回报的身份字段保持 null+原因。

源码定位使用 [实现参考](../IMPLEMENTATION_REFERENCES_ZH.md)：REF-OCR、REF-SPEECH、REF-CODEX、REF-TTS、REF-FONT、REF-TIMELINE、REF-MEDIA、REF-QA。锁定阅读 commit 后仍须单独记录实际部署 commit 和模型权重 revision。

## 4. 验收素材与命令

素材清单包含至少一条单说话者英语原声和一条多说话者素材，并覆盖数字、否定、专名、无字幕讲话、长短自然空档和片尾。跨语言参考音、烧字 OCR 样本、超限批次和错误 ID 用例各自保存固定输入及预期。

项目实现完成后运行：

```bash
python -m mlvideo.cli doctor
codex --version
codex exec --help
python -m pytest tests/unit -q
python scripts/verify_phase.py --phase 2 \
  --fixtures tests/fixtures/manifest.json --out evidence/phase2_run_001
```

`verify_phase.py` 从本地私有配置读取 worker argv、模型目录及登录环境，不把密钥写进素材 manifest。环境探测后先逐个真实模型冒烟，再执行配方；第二期必需模型测试未运行即本期未完成。

逐项断言：

- T-OCR / T-SPEECH：与人工标注分别计算文字、边界、坐标误差；全部人声受到保护。风险片段带具体位置进入 REVIEW。
- T-UTTERANCE / T-TRANSLATE：归句来源可回查；8/9 段、6,000/6,001 字符边界正确；unit_id 守恒；数字/否定等人工复核有记录。
- T-TTS / T-AUDIO：全块拼接的采样数正确；TTS 引用明确的译文、speaker、参考音；人工检查真实内容、音色及片尾。
- T-TIMELINE / T-LAYOUT / T-RENDER：英语尾音完整，中文不重叠下一段原声；时间与字体布局以成片/母版实测，非只看 metadata。
- T-RETRY：挑选一个成功翻译批次和一个成功 TTS 句子分别重试，证明新原始请求响应、新 execution/version、真实调用和固定原输入。
- T-QA：注入错译、字幕越界及缺少人工决定，不能产生虚假 PASS。

证据在 `evidence/phase2_run_001/` 分组保存 translate/tts/ocr/speech/layout/qa，引用资产内不可变原始记录；报告列出每句原文、译文、译音、时间线、字幕和复核的精确 artifact ID。

## 5. 完成与交接

无硬字幕素材真实成片通过本期所需自动检查和人工复核；全链路两次执行不覆盖历史。真实 OCR、ASR/对齐、Codex、CosyVoice3 均有独立运行证据，不能以协议测试数量代替模型可用性。

报告明确“第二期候选成片”，N20 正式发布尚未验收。无硬字幕可依据清点证据标 N13–N15 不适用；第三期整体发布能力仍未完成。交接源码/模型锁、接口 schema、素材和报告到 [第三期 kickoff](PHASE_3_KICKOFF_ZH.md)，参照 [主计划 §12](../DEVELOPMENT_PLAN_ZH.md#12-分期交付与文档完成标准)。
