# 当前方案的实现参考与核查记录

更新：2026-09-11。服务于 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和三期 kickoff。本文件记录阅读依据及需要自行实现的适配，**不是已安装依赖清单、模型可用性报告或部署锁**。

历史来源说明见 [UPSTREAM_REFERENCES_ZH.md](UPSTREAM_REFERENCES_ZH.md)；其中 SQLite、并发与旧执行状态只适用于参考包。新方案以 MySQL 和单主控串行为准。

当前实施状态（2026-09-12）：第一期已验收 PASS，实际部署身份见 `config/upstreams.lock.json`，运行证据见 [最终验收报告](verification/PHASE_1_ACCEPTANCE_ZH.md)。以下源码阅读身份保留为 2026-09-11 的参考记录；第二、三期模型适配仍待开发。

## 1. 本次源码阅读身份

以下文件本次按完整 commit 下载并核对符号，逐文件 SHA-512 与可定位 URL 保存在 [upstream-source-checks.json](verification/upstream-source-checks.json)。前三个历史 pin 重新取回；其他为本次解析的阅读 commit。没有安装这些模型或执行其推理。

| 项目 | 完整阅读 commit | 范围 |
|---|---|---|
| yt-dlp/yt-dlp | `bbc809a1161d3bfca51fa36f59dda35556ee85a0` | `yt_dlp/YoutubeDL.py` |
| YaoFANGUK/video-subtitle-remover | `e109b9ddc1d0e8f153199dfa05c1d767546906d8` | `backend/main.py`, `backend/tools/subtitle_detect.py` |
| YaoFANGUK/video-subtitle-extractor | `85746f7df5bf85978fd05f3ca6ce66e321a87a72` | `backend/main.py` |
| PaddlePaddle/PaddleOCR | `2661c7c0ef5c613e8f93c6e93b2e052399f0f854` | `paddleocr/_pipelines/ocr.py` |
| timminator/VideOCR | `0078547b53609f2810b13eb602a4a67721d57952` | `CLI/videocr_cli.py` |
| m-bain/whisperX | `2cfd7b7c5c7bba144954364db747319b50e8232b` | `whisperx/alignment.py`, `whisperx/diarize.py` |
| QwenAudio/CosyVoice | `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc` | `example.py`, `cosyvoice/cli/cosyvoice.py`, `cosyvoice/cli/frontend.py`, `runtime/python/fastapi/server.py` |
| JeffersonQin/YuzuMarker.FontDetection | `0a94e165fe2b08d2800b723290eabd120b2d3d58` | `demo.py` |
| tkarabela/pysubs2 | `34d6ca5534ffd46cfa460d73aff79521eded2055` | `pysubs2/ssafile.py` |

这些 commit 是代码阅读快照。开发时实际部署的仓库、修改、依赖、镜像 digest、模型 revision 和权重摘要另写 `config/upstreams.lock.json`；不能直接把本表作为目标设备已通过的推荐版本。

## 2. 节点到源码的映射

### REF-CORE：执行、血缘、版本和日志

对应 N02、N20 与主控。阅读本地 [VidFlow 参考包](../sources/video_interleave_v2_design_and_skeleton.zip)，包内前缀 `video_interleave_v2/`，导出声明 commit `374e5d35ad31e039bed2e1aa4dee25f1cc0c9e4c`。

| 文件 | 符号 | 参考范围 |
|---|---|---|
| `vidflow/engine.py` | `Engine.execute/retry`、`Strategy`、`Registry`、`Context.execute` | 每次真实调用、固定输入、输出校验、原始进程日志 |
| `vidflow/store.py` | `Store.ingest/allocate/commit/recover` | 源快照、执行身份与清单思路；重写 MySQL 登记逻辑 |
| `vidflow/pipeline.py` | `run_recipe` | 依赖检查、明确 bindings 和顺序运行 |
| `vidflow/util.py` | `file_lock`、`atomic_json`、`code_provenance` | 本机锁、文件落盘和代码身份 |
| `vidflow/plugins.py` | `worker_strategy` | `--request/--result` 外部协议与标准结果转换 |

不照搬包内 SQLite 表、并发版本分配或恢复状态；新主控遵循主计划的单写入者和单语句保存顺序。

### REF-DOWNLOAD：yt-dlp

[yt_dlp/YoutubeDL.py](https://github.com/yt-dlp/yt-dlp/blob/bbc809a1161d3bfca51fa36f59dda35556ee85a0/yt_dlp/YoutubeDL.py)：`YoutubeDL.extract_info`、`download`、`add_progress_hook`、`post_process`；包内 `vidflow/acquire.py::ytdlp_argv/download` 可参考获取目录和实际进程留证。

直接依赖下载器。新写隔离 acquisition、最终文件探测/SHA-512、失败收据、原声轨选择、禁用重试结果复用。命令参数测试不能代替两次真实下载。对应 N01、T-DOWNLOAD。

### REF-OCR：字幕清点、抽帧与识别

- [paddleocr/_pipelines/ocr.py](https://github.com/PaddlePaddle/PaddleOCR/blob/2661c7c0ef5c613e8f93c6e93b2e052399f0f854/paddleocr/_pipelines/ocr.py)：`PaddleOCR.predict_iter/predict`，包装图像 OCR。
- [CLI/videocr_cli.py](https://github.com/timminator/VideOCR/blob/0078547b53609f2810b13eb602a4a67721d57952/CLI/videocr_cli.py)：`main` 与 `save_subtitles_to_file` 调用，参考 CLI 和抽帧/合并参数。
- [backend/main.py](https://github.com/YaoFANGUK/video-subtitle-extractor/blob/85746f7df5bf85978fd05f3ca6ce66e321a87a72/backend/main.py)：`SubtitleExtractor.run`、`extract_frame_by_fps`、`filter_watermark`、`generate_subtitle_file`，参考视频提取流程。

新写 canonical 坐标和时间、短字幕边界精化、字幕/场景文字区分、标准 CaptionTrack 及原始截图证据。无头 worker 不能保留交互 input 阻塞；默认回答不是可靠适配。两种真实策略按同一源分别执行，不能以返回预设文本的 fixture 冒充 A/B。对应 N05–N06、T-OCR。

### REF-SPEECH：语音边界和说话者

[whisperx/alignment.py](https://github.com/m-bain/whisperX/blob/2cfd7b7c5c7bba144954364db747319b50e8232b/whisperx/alignment.py)：`load_align_model/align`；[whisperx/diarize.py](https://github.com/m-bain/whisperX/blob/2cfd7b7c5c7bba144954364db747319b50e8232b/whisperx/diarize.py)：`DiarizationPipeline`、`assign_word_speakers`。

包装识别/对齐/说话者流程，新增 SpeechTrack、全部原始人声保护、字幕多对多归句和安全切点。对齐失败不能继续声称词边界精确。对应 N07–N08、T-SPEECH、T-UTTERANCE。

### REF-CODEX：本机翻译接口

本次读取 [codex-version.txt](verification/codex-version.txt) 和 [codex-exec-help.txt](verification/codex-exec-help.txt)，版本为 `codex-cli 0.153.4`。已核对 `exec`、`--model`、`-c`、`--json`、`--output-schema`、`--output-last-message`、stdin 和工作目录选项；未调用模型、未将用户认证信息写入证据。

[官方非交互接口](https://learn.chatgpt.com/docs/non-interactive-mode) 说明 JSONL 事件与最终 schema 输出；[GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) 说明模型及 medium 档位。这里引用 CLI 实际帮助与官方接口，没有凭二进制版本推导一个未取得的 Rust 源码 commit。

新写连续完整语句分批、8 段/6,000 字符上限、稳定 ID、最终响应解析、模型回执和失败版本记录。结构化输出仍需业务校验。对应 N09、T-TRANSLATE。

### REF-TTS：官方 CosyVoice3

[example.py](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/example.py)：`cosyvoice3_example`；[cosyvoice/cli/cosyvoice.py](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/cosyvoice/cli/cosyvoice.py)：`AutoModel`、`CosyVoice3.__init__` 和父类 `CosyVoice.inference_zero_shot`；[cosyvoice/cli/frontend.py](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/cosyvoice/cli/frontend.py)：`CosyVoiceFrontEnd.frontend_zero_shot`。

服务封装另参考 [runtime/python/fastapi/server.py](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/runtime/python/fastapi/server.py) 的 `inference_zero_shot/generate_data`，仅作音频接口参考；新方案默认独立顺序 worker，不要求部署这一服务。

`CosyVoice3` 继承 `CosyVoice2`，零样本方法来自 `CosyVoice`；`AutoModel` 按模型配置文件选类。非流式调用仍返回可迭代结果。新写每 speaker 固定参考、准确转录与提示前缀、逐句完整块拼接、原始/规范化音频、真实采样数和实际 backend 回执。

模型保持 `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`。FunAudioLLM 仓库入口当前重定向到 QwenAudio；不据此改写模型 ID。模型权重 revision 和目标设备兼容性尚待第二期验证。对应 N10–N12、T-TTS、T-AUDIO。

### REF-CLEAN：限定区域的字幕清理

[backend/tools/subtitle_detect.py](https://github.com/YaoFANGUK/video-subtitle-remover/blob/e109b9ddc1d0e8f153199dfa05c1d767546906d8/backend/tools/subtitle_detect.py)：`SubtitleDetect.detect_subtitle/find_subtitle_frame_no/unify_regions/split_range_by_scene/find_continuous_ranges_with_same_mask`；[backend/main.py](https://github.com/YaoFANGUK/video-subtitle-remover/blob/e109b9ddc1d0e8f153199dfa05c1d767546906d8/backend/main.py)：`SubtitleRemover.video_inpaint/propainter_mode/sttn_auto_mode`。

参考检测、连续掩膜分组和修复调用，新增 MaskTrack、坐标转换、镜头执行版本与掩膜外差异验证。仅消费输出画面，源音由本项目保管。算法、代码、权重和素材来源分别登记。对应 N14–N15、T-CLEAN。

### REF-FONT：字体与字幕渲染

[demo.py](https://github.com/JeffersonQin/YuzuMarker.FontDetection/blob/0a94e165fe2b08d2800b723290eabd120b2d3d58/demo.py)：`recognize_font`；[pysubs2/ssafile.py](https://github.com/tkarabela/pysubs2/blob/34d6ca5534ffd46cfa460d73aff79521eded2055/pysubs2/ssafile.py)：`SSAFile.save`。另查 [fontTools TTFont.getBestCmap](https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html) 与 [libass/ass_render.c](https://github.com/libass/libass/blob/master/libass/ass_render.c) 的 `ass_render_frame`。

前两项本次固定阅读 commit；fontTools/libass 此处为官方接口/移动源码参考，实际依赖版本仍需锁定。新写重渲染候选比较、中文 fallback、英上中下布局和像素 QA；字形覆盖不代表最终整形正确。对应 N13、N17、T-STYLE、T-LAYOUT。

### REF-MEDIA：FFmpeg

[官方滤镜文档](https://ffmpeg.org/ffmpeg-filters.html)：`trim/atrim`、`setpts/asetpts`、`tpad`、`concat`、`amix`、`subtitles`。本地参考包 `vidflow/media.py::render/read_pcm/write_pcm` 与 `vidflow/demo.py::video_hashes/run_demo` 提供实际母版和验证思路。

直接调用工具，另写 canonical PTS 映射、统一原音画切点、累计采样量化、流式长片处理和真实输出校验。旧渲染器要求每帧整数采样数，不满足全部非整数帧率需求。对应 N03–N04、N12、N18、T-MEDIA、T-AUDIO、T-RENDER。

### REF-TIMELINE：项目自己的时间线规则

参考包 `vidflow/timeline.py::plan/verify` 使用 Fraction、安全帧切点、实际译音采样数和 gap-first 不变量；`tests/test_timeline.py` 提供边界用例参考。

这部分业务规则由本项目实现，不由 FFmpeg 或 ASR 自动决定。扩展非整数帧率时按主计划累计边界量化，再用独立预期验帧/采样，不照搬渲染器的受限条件。对应 N08、N16、T-UTTERANCE、T-TIMELINE。

### REF-QA：媒体与语义证据

参考包 `vidflow/plugins.py::qa_media`、`vidflow/demo.py::run_demo` 和 [QCTools 官方说明](https://github.com/bavc/qctools) 中的 qcli。QCTools 是可选辅助，不是首版必装或语义发布裁判。

新写 required 检查集合、合法定格排除、字幕/译音语义检查、人工决定绑定与发布清单一致性。对应 N19、T-QA。

## 3. 数据库配置依据

MySQL/PyMySQL 官方依据集中在 [主计划 §3](DEVELOPMENT_PLAN_ZH.md#3-mysql文件与索引)。SQL、锁定的 MySQL 镜像及实际恢复路径已在第一期完成实现与验证，证据见 [最终验收报告](verification/PHASE_1_ACCEPTANCE_ZH.md)；历史包的结果不计入这部分验收。
