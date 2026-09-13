# 第二期 kickoff：真实翻译与克隆闭环

状态：2026-09-12 用户确认开始实现，音色方向选用 CosyVoice3 zero-shot；字幕定位按常规 PaddleOCR 路径推进。N05–N12、N16–N19 的候选链路已接入，当前实测和未解决项见 [整体验证记录](../verification/PHASE_2_FULL_VERIFICATION_ZH.md)；[首批 N11/N12 记录](../verification/PHASE_2_VOICE_WORKERS_ZH.md) 保留为历史。第二期正式视频集与人工质量验收尚未完成。基线：[完整开发计划](../DEVELOPMENT_PLAN_ZH.md)，覆盖 REQ-02、REQ-04–06，并沿用 REQ-03 的不可变血缘。

## 当前冻结与收尾（2026-09-12）

2026-09-13：已执行容量受限的第一轮预处理与音色试听，88 项回归通过；前 117 秒双语候选和整体验收尚未完成，见 [第一轮报告](../verification/PHASE_2_ROUND1_20260913_ZH.md)。

本次收尾已实现绑定具体输入的说话人/参考音复核入口及逐条音色检查，87 项回归通过；整片候选与整体质量验收尚未完成。实际分组/参考音缺项和容量记录见 [收尾报告](../verification/PHASE_2_CLOSEOUT_ZH.md)，不能标记第二期 PASS。

用户已接受三段预览的当前方向，并要求 commit & push；这次提交冻结实现与已有证据，不等同于第二期整体 PASS。已确认画面内字幕、原英文保留、中英文同步显示和逐段英中衔接；音色/说话人库可靠性、真实整片与完整质量验收仍需收尾。第三期前置条件与衔接见 [第三期 kickoff](PHASE_3_KICKOFF_ZH.md)。

## 0. 编码前的对比与选型门槛

按 2026-09-12 的要求，先对比以下候选，随后用户试听偏好明确为 CosyVoice3 zero-shot，并确认可开始。真实视频样本覆盖、前导杂音处理和最终听感验收继续作为交付门槛，不再阻止开始编码；不能因此宣称这些检查已通过。

| 对比 | 候选 A | 候选 B | 固定输入与主要指标 |
|---|---|---|---|
| PaddleOCR 两种业务接口 | `58080 POST /layout-parsing`，VL 版面解析 | `58081 POST /ocr`，常规检测识别 | 同一批真实烧字视频帧：文字 CER/WER、字幕漏检/误检、框 IoU、阅读顺序、失败率、单帧耗时 |
| 跨语言音色克隆 | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | 相同英语参考音、准确转录和中文目标句：内容准确性、音色相似度、自然度、截尾/重复、失败率、完整生成耗时与 RTF |

Qwen 使用支持参考音克隆的 Base，不能以 CustomVoice 的预设 speaker 代替。参考 [Qwen 官方说明](https://github.com/QwenLM/Qwen3-TTS) 与 [CosyVoice 官方说明](https://github.com/QwenAudio/CosyVoice)。原生精度、量化、MLX/PyTorch/vLLM 和硬件分别记录；设备或精度不同时，只报告对应部署组合表现，不将速度差异归因于模型本身。

执行顺序及可复核条件：

1. 环境探测：确认服务主机、实际路由/schema、镜像 digest、部署代码、模型 revision、设备和参数；真实请求成功才标记可调用。缺失配置或模型报错保留原始错误，不算质量测试通过。
2. 冻结样本：OCR 至少 20 帧，覆盖单/双行、小字、复杂背景、数字/专名和无字幕负例；人工标注文字与字幕框。TTS 至少 2 位说话者，每人 6 条中文目标句，覆盖长短句、数字、否定与专名；固定参考音区间、转录和 SHA-512。合成 fixture 不替代真实语音。
3. 串行实测：两路使用相同输入字节；保留接口所需的参数差异及所有原始响应，不只保存整理后的文本。每路首次请求单列（无法确认冷启动则标注首次请求），随后每样本重复 3 次；统计中位数、范围和全部失败，不挑选最好结果。TTS 拼接全部返回块，保存原始音频、采样率、样本数、完整请求耗时及 `RTF=耗时/音频时长`；试听副本使用一致响度处理并保留处理记录。
4. 逐项对比：OCR 保存原图、框叠加图、人工标注与两路文本；坐标无法取得时标为缺失。TTS 保存匿名 A/B 试听对和映射，人工评价音色/自然度/尾音，固定 ASR 的识别误差只作辅助指标，不冒充人工真值。框 IoU 按匹配字幕行计算，并分别统计未匹配真值和预测框。
5. 选型：提交逐样本结果、耗时分布、失败样本、试听文件和适用范围，并记录用户决定。用户已允许进入实现；未完成测试保留为待验收，不能由“允许编码”推导为“质量通过”。

每次测试独立保存到 `evidence/phase2_compare_<run_id>/`，包括输入清单及哈希、环境身份、请求/响应、音频/叠加图、指标、人工复核与选型记录；再次测试建立新目录。测试产物与第二期端到端验收分开，不能据此宣布第二期完成。

部署检查以 [Compose 文件](../compose-paddle-ocr.yaml) 为准：两路当前都声明 `device_ids: ["0"]`，常规 OCR 命令也是 `gpu:0`，不能按 GPU1 注释认定两块 GPU 隔离；测量时串行调用并记录 GPU 竞争。镜像标签变量、`VLM_BACKEND` 和挂载的 `vllm_config.yaml` 需要从实际部署获取；该 NVIDIA Compose 不能直接在 Mac 上照搬运行。接口契约参考 [OCR 官方文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md) 和 [VL 官方文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)，最终以服务真实 schema 为准。

2026-09-12 本机初查时两个端口均连接失败，默认 Hugging Face 缓存仅有 Qwen CustomVoice。随后按用户要求检查 `cartoon-tts-root`，复用 OCR 环境，在 Mac 部署两种 PaddleX 原始接口，并在挂载盘下载两种克隆候选。最新实测、证据和未完成项见 [本轮比较报告](../verification/phase2_compare_20260912_001.md)。测试结果不自动等于最终选型。

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
| 6 | N10–N11 参考音与克隆 | speaker 参考音+译文 → RawDubClip；采用选定的克隆模型，逐句完整音频 | T-TTS |
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
| TTS | CosyVoice3 zero-shot；逐句、原速、`stream=False`，读取全部返回音频；锁定实际参数和模型 revision；前导异常进入 REVIEW，禁止固定时长盲裁 |
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
  --config config/local-phase2-001.json \
  --fixtures evidence/phase2_inputs_001/manifest.json --out evidence/phase2_run_NEW
```

第一期的 `tests/fixtures/manifest.json` 不能替代第二期素材；第二期 manifest 必须为 `phase=2`，每个 sample 固定 path、SHA-512、来源说明和 settings。上述本机样本为真实英语音频配控制背景，仅用于技术联调，不满足本节真实视频集要求。

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

无硬字幕素材真实成片通过本期所需自动检查和人工复核；全链路两次执行不覆盖历史。真实 OCR、ASR/对齐、Codex、选定的克隆模型均有独立运行证据，不能以协议测试数量代替模型可用性。

报告明确“第二期候选成片”，N20 正式发布尚未验收。无硬字幕可依据清点证据标 N13–N15 不适用；第三期整体发布能力仍未完成。交接源码/模型锁、接口 schema、素材和报告到 [第三期 kickoff](PHASE_3_KICKOFF_ZH.md)，参照 [主计划 §12](../DEVELOPMENT_PLAN_ZH.md#12-分期交付与文档完成标准)。
