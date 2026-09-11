# YouTube 双语视频流水线开发设计

> 历史参考，2026-09-11 补充说明：本文原文来自 [bivideo_design_and_reference_v2.zip](../sources/bivideo_design_and_reference_v2.zip)，内部路径相对于包内 `video_pipeline_v2/`。实现/测试状态均指该参考包，未在本次更新中重跑；当前 MySQL 串行方案见 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和 [文档入口](README_ZH.md)。下方保留原始内容。

## 1. 目标与交付边界

构建一个新的、轻量的 Python 媒体处理项目。不 fork 整套翻译配音应用作为主框架；只直接依赖成熟的下载、媒体和字幕库，并将 OCR、视频修复、语音分析等封装为可替换节点。字幕翻译及音色克隆/多语言语音生成通过模型接口完成，相关整合应用仅作接口设计参考。

最终播放规则：一段英文原声结束后等待 1 秒，播放对应译文音频；原画面优先继续播放。译文及其后 1 秒无法全部容纳在下一段英文之前的原始空档时，只延长下一段英文开始前正在显示的最后一帧。原始音轨与原片进度同步暂停/恢复。中文之后至少留 1 秒；原始空档更长时保留多出来的画面和停顿，不缩短原片。

本设计覆盖下载、归档、字幕提取和擦除、语音边界、样式恢复、模型调用、时间线、双语渲染、质量检查、版本、重试和测试。首版不承诺口型同步、被字幕遮挡背景的真实无损还原、只凭像素唯一识别字体、多说话者重叠语音的全自动完美处理。

随附代码是**可执行的架构参考骨架**，已经实现内容寻址目录、节点执行版本、输入/输出血缘、显式选择版本、强制重试、外部 worker 协议、yt-dlp 下载适配器、空档优先时间线和真实 FFmpeg 合成验证。**没有实现真实 OCR、去字幕、自动字体匹配、翻译、克隆 TTS 或完整质量评估适配器。** 测试字幕插件及提示音不得被解释为这些 AI 功能已经完成。

### 1.1 十项要求的落实位置

| 要求 | 设计落点 | 参考骨架状态 |
|---|---|---|
| 项目骨架 | 第 2 节；`src/bivideo/` | 已有可运行 CLI、存储、执行器、插件、时间线和媒体模块 |
| 完整业务流程 | 第 3 节；逐节点契约及门禁 | 全流程已设计，AI 节点待实现 |
| 源视频 SHA-512 聚合 | 第 4 节；下载临时目录归并机制 | 本地导入已验证；下载代码已写、联网下载未验证 |
| 每次节点执行版本与血缘 | 第 5–6 节 | 已验证已实现的参考节点；完整业务 schema 待逐一接入 |
| 节点策略可替换 | 第 7 节；标准契约 + worker 协议 | 两个字幕测试插件与同一个下游、外部进程已验证 |
| 无条件重试 | 第 8 节 | 已验证同输入、同参数、同输出内容仍再次执行 |
| 每视频汇总日志 | 第 9 节 | 已实现 JSONL + SQLite 事件索引；日志投影重建已验证 |
| 策略版本 | Git commit、策略 ID/版本、代码树哈希、有效参数、模型/环境锁 | 主项目代码身份已有；生产模型与第三方 worker 的运行身份校验待接入 |
| 参考库及代码片段 | `CODE_REFERENCES_ZH.md`、`references.catalog.json` | 已给出真实路径、函数及短摘录；上游 commit 需部署时锁定 |
| 单测和真实集成测试 | 第 14 节、`REAL_TEST_MATRIX_ZH.md`、`tests/` | 已执行的结果见 `verification/TEST_REPORT_ZH.md`；未执行项目明确区分 |

## 2. 项目骨架

### 2.1 总体结构

采用“控制面 + 媒体执行层 + 模型 worker”三层。控制面不依赖 Paddle/PyTorch/MLX，不直接内嵌复杂模型代码。Python 包 entry point 可用于发现已安装插件；默认只加载明确配置的可信插件，而不是自动执行任意下载代码。Python 的 entry point 机制可直接复用。[^R13]

```text
bivideo/
├── src/bivideo/
│   ├── cli.py                 # URL/本地导入、节点执行、重试、历史和演示
│   ├── acquire.py             # yt-dlp 特殊前置节点；SHA 未知时的获取记录
│   ├── store.py               # SHA-512 存储、SQLite 索引、ArtifactRef、选择版本
│   ├── contracts.py           # 节点接口、产物引用、基础语义校验
│   ├── engine.py              # 版本分配、执行、验证、发布、重试
│   ├── worker.py              # 外部进程协议、超时与进程组终止
│   ├── plugins.py             # ffprobe、时间线插件与契约测试插件
│   ├── timeline.py            # 空档优先规划；纯函数，不调用模型
│   ├── media.py               # 真实 FFmpeg 参考合成及验证工具
│   ├── demo.py                # 可复现的合成示例；不含真实语音
│   └── util.py                # 原子写入、哈希、路径限制、代码身份
├── schemas/                   # 机器可读 schema；语义约束仍需代码校验
├── configs/                   # pipeline/策略/worker/测试配置
├── scripts/                   # worker 示例、上游锁定、构建身份
├── tests/                     # 单测、真实媒体、条件式联网/模型测试
├── docs/                      # 设计、代码参考、真实环境测试矩阵
└── verification/              # 实际执行报告、JUnit、演示视频目录
```

生产版本在相同核心之上补充以下模块，不需要推倒现有存储协议：

```text
orchestration/  dag.py, scheduler.py, selection.py, recovery.py, resources.py
adapters/      ytdlp, ffmpeg, subtitle_tracks, videocr, vse, paddle, vlm,
               whisperx, vsr, font_resolver, translation_api, speech_api, qcli
nodes/         normalize, frame_index, text_regions, ocr, speech, utterances,
               masks, inpaint, clean_qa, style, translate, voice_refs,
               synthesize, dub_normalize, dub_qa, layout, render, final_qa, publish
models/        gateway.py, invocation_store.py, credentials.py, model_lock.py
quality/       contracts.py, structural.py, audio.py, subtitles.py, visual.py
```

### 2.2 依赖与部署原则

主程序使用 Python 3.11+；初期 CLI + 单机 SQLite 足够，不引入 Airflow、Dagster、Temporal 或任务缓存框架。数据库及执行目录放在同一主机的本地磁盘；SQLite WAL 不适用于多个主机共享同一网络文件系统的部署。[^R14]

OCR、视频修复和语音模型分别放入独立虚拟环境、容器或局域网 worker。Mac 主机可负责控制面和媒体合成，模型 worker 可替换到其他设备。此处是部署设计，不是模型在特定 Apple Silicon 上已经实测的性能承诺。

资源调度：每个大模型设备初始并发 1；下载并发、FFmpeg 并发、CPU OCR 并发独立配置。模型可以保留已加载权重；不允许把先前节点的推理结果当作本次重试结果返回。

## 3. 业务流程与每个节点的职责

### 3.1 DAG

```text
YouTube URL ─ download(yt-dlp) ─ ingest_sha512 ─ probe ─ normalize ─ frame_index
本地视频 ────────────────────────┘                         │
                    ┌────────────────────────────────────┼─────────────────────┐
                    ↓                                    ↓                     ↓
               subtitle_tracks                      text_regions          speech_analysis
                    │                                    ↓                     │
                    └─────────── caption_extract / OCR ──┘                     │
                                             │                                │
                           ┌─────────────────┼────────────────────────────────┘
                           ↓                 ↓
                      style_resolve     utterance_build
                           │             │           │
                     mask_build         translate   voice_reference
                           ↓             │           │
                        inpaint          └─ synthesize[unit,language]
                           ↓                         ↓
                        clean_qa                dub_normalize → dub_qa
                           │                         │
                           └──────────────┬──────────┘
                                         ↓
                                  timeline_gap_first
                                         ↓
                                  subtitle_layout
                                         ↓
                                      render
                                         ↓
                                     final_qa
                                         ↓
                              publish / REVIEW / FAIL
```

`mask_build` 实际依赖文字区域/字幕证据，不依赖字体匹配成功；上图只是收拢显示，生产 DAG 必须按下表真实输入配置。`timeline_gap_first` 不依赖修复模型结果才能计算，但 `render` 必须同时持有清理视频、原音、已验收译音和布局。允许这些独立分支并行，禁止靠文件名约定隐式串接。

### 3.2 逐节点输入、输出与门禁

| 节点 | 做什么 | 标准输出 | 必要检查与复用边界 |
|---|---|---|---|
| `download` | 获取一个 YouTube 视频、原有字幕和描述性元数据 | `AcquisitionReceipt.v1` + 本地媒体/旁挂文件 | yt-dlp 直接依赖；处理结束码、实际文件、媒体流；不以 URL 当内容哈希 |
| `ingest_sha512` | 在复制已完成媒体时流式算 SHA-512；归档全部已知执行证据 | `SourceAsset.v1` | 新开发；源 bytes 不修改，重试导入仍重新执行读取/哈希 |
| `probe` | 读取视频/音频/字幕流、PTS、色彩、旋转、音轨语言 | `MediaProbe.v1` | FFprobe；缺视频/缺所需音轨/直播未结束等为阻断条件 |
| `normalize` | 产生明确时钟的工作视频、48 kHz 工作音轨及映射 | `NormalizedMedia.v1` | FFmpeg；保留原件；记录 CFR 转換、旋转、色彩及声道策略 |
| `frame_index` | 保存帧 PTS、原片映射；按需求抽取证据图 | `FrameIndex.v1` | 复用解码器，索引/产物管理新开发 |
| `subtitle_tracks` | 提取容器或旁挂 SRT/VTT/ASS | `CaptionCandidates.v1` | 不把有软字幕视为没有硬字幕；文本与视觉证据分别确认 |
| `text_regions` | 检出目标字幕区并关联时序，区分字幕和场景文字 | `TextRegions.v1` | 参考 VSR/VideOCR 检测；未确定区域则 REVIEW，禁止默认擦全屏 |
| `caption_extract` | 读取软字幕或 OCR 原画面，合并重复显示事件 | `CaptionSet.v1` | VideOCR/VSE/Paddle/VLM 可替换；必须保留原图、区域及置信度 |
| `speech_analysis` | 原音所有讲话的 VAD、ASR、词级对齐、说话者与重叠区 | `SpeechMap.v1` | WhisperX 等；背景音乐不是讲话；无字幕讲话也要受保护 |
| `utterance_build` | 关联字幕和原音，分成完整朗读单元 | `UtteranceSet.v1` | 新开发；一个单元可含多个字幕事件；重叠未解决禁止规划 |
| `style_resolve` | 原字体候选、视觉参数及中文字体映射 | `StyleSet.v1` | fontTools + 字体识别参考；准确/近似/无法确定必须区分 |
| `mask_build` | 按目标字幕、场景与字框生成修复掩膜 | `MaskSet.v1` | 参考 VSR；包括描边/阴影扩张、场景边界约束；不要依赖 OCR 文本全对 |
| `inpaint` | 修复被字幕覆盖区域，输出不含字幕的画面 | `CleanVideo.v1` | VSR worker；仅消费画面，不依赖其回填原音逻辑 |
| `clean_qa` | 清理视频的残字、非目标改动及明显闪烁检查 | `QualityReport.v1` | 基于未重加字幕的 clean video；检查原片/修复片对应源时间 |
| `translate` | 用模型翻译完整单元，保留术语与上下文 | `TranslationSet.v1` | 外部模型接口；原文、提示词、请求/响应、模型版本完整留存 |
| `voice_reference` | 同一说话者内选择参考音频；输出准确转写和截取范围 | `VoiceReferenceSet.v1` | 边界/选取策略新开发；音色生成本身交给模型 |
| `synthesize` | 对每个单元、每种目标语言生成原始音频 | `DubRaw.v1` | 外部语音模型；不变速、不裁词尾；每个单元独立版本 |
| `dub_normalize` | 解码/重采样，校验静音边缘，测量实际采样数 | `DubClipSet.v1` | FFmpeg；记录裁去的是静音还是语音，不能静默裁内容 |
| `dub_qa` | 译音回识别、语言、重复/漏词、峰值/静音、音色异常 | `QualityReport.v1` | 无需完整替代人工；未执行不能标 PASS |
| `timeline_gap_first` | 用实际译音长度占用空档，只补不足的定格 | `Timeline.v1` | 新开发确定性纯函数；不由模型自由决定 |
| `subtitle_layout` | 将英文与中文映射到新时间线，英文在上中文在下 | `SubtitleLayout.v1` + ASS | pysubs2/libass；需新开发布局、换行、字体覆盖与越界检查 |
| `render` | 原片/原音暂停恢复，插入译音，烧录或输出外挂字幕 | `RenderedMedia.v1` | FFmpeg；全部时长显式规划，禁止 `-shortest` 掩盖丢尾 |
| `final_qa` | 完整解码、时间线、原音保留、字幕、音色和视觉抽检 | `QualityReport.v1` | 检测器复用，业务门禁新开发 |
| `publish` | 固化选用版本图，生成成片、字幕、报告、可审计血缘清单 | `Delivery.v1` | 新开发；不覆盖先前最终结果，不自动上传 YouTube |

### 3.3 软字幕、无字幕、无讲话分支

软字幕文本优先作为候选，但仍检测原画面是否烧字。没有硬字幕时，`inpaint` 执行 `verified_passthrough` 策略：读取并验证输入，然后发布引用清楚的新执行结果，不伪造一次修复推理。

没有可见字幕但有英文讲话时，可配置 ASR 生成字幕；记录 `text_source=asr`，不能称为“原字幕提取”。没有讲话的视频输出 `REVIEW/NOT_APPLICABLE` 及证据，不凭空生成译音。任何必需节点不适用，都应保存已执行判断的版本；“不适用”不等于缓存跳过。

## 4. SHA-512 聚合与目录设计

### 4.1 身份定义

`asset_sha512 = SHA512(下载完成并合并音视频之后、任何归一化或修改之前的原始媒体文件字节)`。

同 bytes、不同文件名/路径/URL，聚合到同一目录。相同 YouTube 视频选择不同分辨率、编码、音轨或被重新封装后 bytes 不同，会产生不同 SHA-512，因此属于不同源资产。YouTube ID 仅作为来源关系索引，不能取代 SHA-512。可另记 `same_external_video_id` 关系，但不自动合并两份不相同 bytes 的处理历史。

### 4.2 下载前无法知道源 SHA-512

下载必须先进入唯一临时获取目录。只有完整下载、容器合并及流验证完成后，才计算最终媒体的哈希并归并：

```text
data/
├── acquisitions/
│   └── <acquisition_id>/             # SHA 未知，或下载失败尚无完整文件
│       ├── request.json
│       ├── runtime.json
│       ├── stdout.txt / stderr.txt
│       ├── source.* / *.part
│       └── result.json
└── videos/
    └── <128字符SHA512>/
        ├── asset.json
        ├── source/original.bin        # 原件；扩展名不用于身份判断
        ├── acquisitions/<id>/         # 归并后的下载/导入记录、旁挂字幕、元数据
        ├── pipeline.jsonl             # 该源视频唯一主汇总日志
        ├── index.sqlite               # 可查询的执行、产物、边、事件与选择索引
        ├── nodes/
        │   ├── ocr/all/v000001_<attempt-id>/
        │   ├── ocr/all/v000002_<attempt-id>/
        │   ├── synthesize/u000018.zh-CN/v000001_<attempt-id>/
        │   └── render/all/v000001_<attempt-id>/
        ├── selections/<branch>/<selection-id>.json
        ├── pipeline_runs/<run-id>/     # 生产调度器待增加
        ├── comparisons/<comparison-id>/
        └── deliveries/<delivery-id>/
```

失败下载没有源视频可计算哈希，不能强行分配到某个源资产目录。后续同一获取任务成功时，可将明确相关的失败尝试归并过去，同时保留原 acquisition ID、时间戳、日志事件 ID 和迁移映射；无法确认同一内容的失败获取仍留在 `acquisitions/`。这是身份建立前不可避免的例外，而不是用 URL 哈希假装视频哈希。

每次下载重试使用新目录，默认不加载 download archive、不复用已有完整文件、不把旧 `.part` 自动当作本次执行的最终结果。分片级网络重试与整个下载节点重试分别记录。默认关闭内部下载重试，可由控制面创建新的下载版本；生产若启用网络传输重试，需保存每次请求/分片尝试记录。yt-dlp 的进度钩子和下载后处理接口可复用。[^R01]

### 4.3 归并、并发与重试语义

先复制并边复制边哈希，避免“先算哈希、原件被外部修改、再复制”导致目录名与内容不同。复制前后检查源文件 size/mtime；成品验证摘要后再原子认领。存在同 SHA 目录时验证已有原件，绝不覆盖内容不同的文件。并发同源导入共享同一资产锁，分别保留自己的获取执行记录。

`.bin` 是骨架中的固定存储名，FFprobe 可按内容识别；生产适配器须使用已归一化且有明确容器扩展名的工作产物，不能将该名称直接传入只按扩展名接受视频的上游 GUI/CLI。

模型权重、可执行文件、系统字体属于共享依赖，可放在只读依赖仓库中，按摘要引用；它们不是本视频生成的中间产物。所有视频专属的帧图、掩膜、字幕、模型请求响应、参考音频、生成音频、尝试产物和最终文件均位于源资产目录。字体文件不随本交付包分发。

## 5. 版本与数据血缘

### 5.1 不混用五种身份

| 身份 | 含义 | 示例 |
|---|---|---|
| `asset_sha512` | 原媒体 bytes 的内容身份 | 128 位十六进制文本 |
| `pipeline_run_id` | 一次完整流程编排或实验分支的执行 | UUID；不可作为源视频目录键 |
| `attempt_id` | 某节点某单元的一次实际业务执行 | UUID；输入相同也必须不同 |
| `version` | 同源、同节点、同单元的可读递增版本号 | v000001 / v000002；不同策略共用这条序列 |
| `artifact_sha512` | 某个实际产出文件的内容摘要 | 两次执行可相同，但执行版本仍不同 |

`contract_version` 是数据结构版本，`strategy_version` 是业务策略版本，`git_commit` 是代码版本，不能用其中一个替代其他字段。`u000018` 只在一个指定的 `UtteranceSet` 版本内有效。跨分句版本的引用必须同时指明上游产物；禁止用相同局部编号直接连接不同 OCR/分句分支。

### 5.2 每个 attempt 的目录

```text
v000003_<attempt-id>/
├── request.json               # 输入引用、有效参数、策略与代码身份；开始后不可改
├── runtime.json               # 工具版本、依赖锁、设备、实际加载模型信息
├── work/                      # 本次独占目录；原始模型响应、证据、临时文件均保留
├── artifacts/                 # 经校验发布给下游的文件
├── result.json                # 终态清单、输出引用、每个文件摘要、用时、错误/状态
└── error.txt                  # 失败时保留；发布前脱敏
```

禁止任何节点写 `ocr/output.srt` 之类跨版本公共可变文件。第三方项目内置的 `output/<视频名>` 路径必须重定向到当前 `work/`，或在每次执行独占的工作副本中调用。所有已落盘文件进入文件清单；大量帧/掩膜可使用 `files.jsonl`，每行包含相对路径、SHA-512、大小、媒体类型及用途，而不是只给整个目录一个哈希。

已发布目录不允许后续节点更改。失败目录也保留，不自动清空。临时文件仅指当前执行尚在写入的工作文件，不意味着失败后可以抹掉证据。输入若需要在适配器内变更，应先复制到本次 `work/`。

### 5.3 精确的 ArtifactRef

```json
{
  "asset_sha512": "<source SHA-512>",
  "producer_attempt_id": "<OCR attempt UUID>",
  "name": "captions",
  "schema": "captions.v1",
  "path": "nodes/ocr/all/v000003_<id>/artifacts/captions.json",
  "sha512": "<this file SHA-512>",
  "bytes": 12345
}
```

执行前校验资产一致、生产 attempt 已发布、名称与数据库记录一致、相对路径不越界、文件类型/大小/摘要正确。不能只比较路径，不能接受伪造 producer ID，也不能把目录中刚好叫 `latest.json` 的文件当作依赖。

### 5.4 血缘清单示例

以下是生产请求的目标格式，不表示骨架已实现所有字段的运行采集：

```json
{
  "schema": "node_request.v1",
  "asset_sha512": "<source digest>",
  "pipeline_run_id": "<run UUID>",
  "attempt_id": "<attempt UUID>",
  "node": "ocr",
  "item": "all",
  "version": 3,
  "retry_of": "<previous attempt UUID or null>",
  "strategy": {
    "strategy_id": "paddle_ocr",
    "strategy_version": "2.1.0",
    "plugin_contract": "node-plugin.v1",
    "git_commit": "<40/64 hex git object id>",
    "code_tree_sha512": "<actual source snapshot digest>",
    "dirty": false,
    "upstream_repo": "PaddlePaddle/PaddleOCR",
    "upstream_commit": "<resolved immutable commit>",
    "dependency_lock_sha512": "<lock digest>"
  },
  "models": [{
    "role": "ocr_recognition",
    "provider": "local",
    "model_id": "<configured model identifier>",
    "revision_requested": "<pinned revision>",
    "revision_observed": "<observed revision or null>",
    "weights_sha512": "<weights digest or null>",
    "quantization": "<effective setting>",
    "runtime": "<framework/backend version>",
    "device": "<actual device>"
  }],
  "effective_params": {
    "detector_threshold": 0.5,
    "recognition_threshold": 0.75,
    "region_policy": "subtitle_only",
    "frame_sampling_policy": "coarse_to_fine_v1"
  },
  "inputs": {"media": "<ArtifactRef>", "regions": "<ArtifactRef>"},
  "prompt_ref": null,
  "secret_refs": [],
  "cache_policy": "FORBID_RESULT_REUSE"
}
```

必须记录默认值展开后的有效参数；OCR 缩放、抽帧频率、合并阈值、种子、模型采样参数、语言和音色参考都属于血缘。参数 canonical JSON 禁止 NaN/Infinity；摘要只用于比较和审计，不作为跳过执行的条件。

模型调用另保存 `invocations/<invocation_id>/request.json`、`response.json`、服务端 request/job ID、开始结束时间、耗时、用量/费用（能获取时）、状态和重试关联。API 密钥、Cookie、Authorization、签名下载链接不写入普通日志；使用密钥引用或脱敏描述。原始响应需要保护性存储，不能以“要记录完整参数”为理由泄露凭据。

云端模型不能确认实际权重版本时写 `revision_observed=null`、`reproducibility=provider_unpinned`，不得将请求的模型别名当作实际权重哈希。记录血缘不等于承诺字节级可重现。

## 6. 持久化事务、失败与恢复

### 6.1 发布协议

1. 在 SQLite 短事务中分配 attempt、版本号及独占目录，状态 RUNNING。
2. 原子写入并 fsync 请求和环境快照，写 `NODE_STARTED`。
3. 校验输入引用及 schema；调用业务策略；原始输出留在 `work/`。
4. 校验业务结果、所有输出文件及输入摘要，生成完整文件清单。
5. 将发布文件写入本次 `artifacts/`；fsync 文件与目录；写终态清单。
6. SQLite 事务中登记产物和边、将 attempt 标为 SUCCEEDED，供下游可见。
7. 汇总日志写 `NODE_SUCCEEDED`。若日志写入中断，数据库事件可重建 JSONL 投影。

文件系统与 SQLite 不是一个原子事务，必须设计恢复窗口，不能只写“原子保存”四个字。清单已完整持久化但 SQLite 尚未发布的情况属于 PREPARED/RECOVERY_REQUIRED：恢复器在确认原进程已死亡、所有摘要和清单有效后完成元数据发布；不是一次新的业务重试。没有完整清单的遗留 RUNNING 标为 ABORTED，用户重试时创建新 attempt。

若需要恢复器，需单独实现 heartbeat、owner PID+启动时间/主机 ID、lease expiry 与进程存活判断；不能仅凭旧 mtime 判定任务已经死亡。**骨架已有日志投影重建，尚未提供生产级自动孤儿任务恢复器。**

### 6.2 一致性规则

下游只能读取已发布产物。任务失败、worker 超时、输出 JSON 无效、文件摘要错误时，不发布部分成功产物；保留已生成文件用于检查。能够独立发布的分块应明确建成独立 item attempt，而不是把一个失败节点的半成品伪装成完整结果。

版本分配使用数据库事务，不采用“扫描目录最大值 + 1”。同源多实验可以并行，输出依靠 UUID 隔离；同一 GPU 的占用由资源调度器约束，不需要把整个源目录锁到模型执行结束。

每个源目录的 SQLite 是索引，文件清单与请求/结果是可重建事实记录。生产需实现全目录校验和索引重建，检测“DB 有但文件没了”及“文件完整但 DB 未发布”。持久化测试必须故障注入，不能只测正常退出。

## 7. 插件与标准契约

### 7.1 节点和策略分离

节点表达业务语义，例如 `caption_extract`；策略表达实现，例如 `paddle_ocr`、`qwen_vl_ocr`、`videocr_adapter`。下游只依赖 `CaptionSet.v1`，不 import 某个 OCR 实现的内部返回类。

```python
class NodeStrategy(Protocol):
    spec: PluginSpec

    def execute(
        self,
        context: NodeContext,
        inputs: dict[str, ArtifactRef],
        effective_params: dict,
    ) -> NodeResult:
        ...
```

控制面负责版本、血缘、输出路径、状态和日志；插件负责业务。禁止插件自行决定“输入未变，跳过”、覆盖其他 attempt、修改输入、挑选下游版本或发布最终结果。

### 7.2 独立进程协议

每个 worker 接收唯一的 `--request <path>`，stdin 关闭；返回 `worker_response.json`，其中必须回传 `attempt_id`、执行状态、实际策略/模型身份、`execution=EXECUTED`、文件清单和结构化质量问题。文件路径相对于本次工作目录，不接受逃逸路径和符号链接。

主进程检查超时、退出码、协议、文件内容及摘要；JSON 合法不等于业务成功。模型 API 层为每个新 attempt 创建新调用身份；远程长任务可在同一次 attempt 内轮询 job ID，网络读取重试不应误算成重新生成。

骨架的 `SubprocessWorker` 已实现请求/响应、关闭 stdin、超时杀进程组和基础校验；它运行的是可信本地程序，**不是安全沙箱**。生产要隔离不可信第三方代码，需要容器/操作系统权限边界、只读输入挂载、仅允许写本次工作目录和显式网络策略。

### 7.3 OCR A/B 实验

同一 `NormalizedMedia`、`FrameIndex`、目标区域证据固定为输入版本，分别执行 A/B 策略。A/B 输出不同执行版本；各自的下游处理分支记录自己选用的引用。比较节点读取两份 `CaptionSet` 及同一金标准，按文本准确性、区间误差、字幕漏检、框定位、耗时和资源使用输出报告。

原始 VLM/OCR 响应必须保存。标准化适配层不得把缺失 bbox 伪造成全屏、把缺失置信度伪造成 1.0。schema 允许 `bbox=null` / `confidence=null` 的生产形式，但下游需要这些字段时必须明确阻断或请求补充证据。骨架的字幕测试 schema 使用已知 bbox 和置信度，只验证契约替换，不代表已覆盖所有生产缺失值情形。

### 7.4 核心契约字段

| 契约 | 必要字段 | 关键语义 |
|---|---|---|
| `NormalizedMedia` | 原始 ArtifactRef、视频/音频 ArtifactRef、clock_id、帧率分子分母、采样率、原始到归一化映射 | 不能只给 duration/fps 浮点值 |
| `CaptionSet` | canvas、clock_id、cues[id,text,start,end,bbox,confidence,source,evidence_refs] | 标注时间属于哪个视频版本、坐标属于哪个分辨率 |
| `SpeechMap` | spans、words、speaker、overlaps、alignment confidence、全部语音覆盖范围 | 没有字幕的讲话不能被丢弃 |
| `UtteranceSet` | local id、source CaptionSet/SpeechMap、cue_ids、speech_start/end、speaker、safe boundaries | 新分句版本产生新单元作用域 |
| `StyleSet` | 每样式的字体候选、选中字体摘要、中文字形覆盖、字号/颜色/描边/阴影、匹配状态 | 不确定就保留不确定 |
| `TranslationSet` | unit scope/id、语言、display_text、spoken_text、术语/专名映射、模型调用引用 | 展示文字和朗读文字可以不同，差异须显式 |
| `VoiceReferenceSet` | speaker、原音范围、参考音频、准确转写、质量指标、证据 | 不拼入别人的声音凑时长 |
| `DubClipSet` | unit scope/id、目标语言、WAV 引用、采样率/数、有效发声范围、裁边记录、模型调用引用 | 时间线使用实际测量结果 |
| `Timeline` | 原/输出时钟、源片段映射、hold、原语音、译音、字幕关联、预期总帧/采样数 | 不以 SRT 的时间直接当音频切点 |
| `QualityReport` | 检查器版本、输入版本、每项状态/阈值/证据、源和输出时间、受影响 artifact | 未执行和通过严格区分 |

字幕 JSON 中的 `start_us/end_us` 适用于视觉事件；音频边界用 `start_sample/end_sample`；原片视频使用帧 PTS/索引。所有区间使用左闭右开 `[start,end)`。转换规则写入 `clock_id`，不要让每个 worker 自行用浮点秒四舍五入。

## 8. 重试、恢复与版本选择

### 8.1 强制重试规则

每次 `retry` 都创建新 attempt/version、请求/结果文件和日志，校验相同的固定输入后调用策略。即使输入摘要、参数、Git commit 和上次输出 bytes 全相同，也不能返回旧产物代替执行。

允许的优化：保留已加载模型权重、使用连接池、复用不可变字体库和下载好的模型文件。禁止的优化：节点结果缓存、全局文字→语音结果缓存、“输出文件存在即完成”、相同参数直接读取上次 OCR、同源视频已处理所以忽略重试。

远端提供者是否内部复用了结果有时无法观察。适配器必须实际发出新业务请求、使用新的 request/idempotency scope，并保存响应中的实际可见信息；不能保证提供者在不可观察的内部重新进行了全部计算。无条件重试不意味着忽略无效输入、凭据缺失、权限、资源或安全检查，这些情况下应保存失败而非假执行。

### 8.2 明确区分四个动作

| 动作 | 是否调用业务 | 结果 |
|---|---|---|
| `run / retry` | 必须调用；校验失败则显式失败 | 新 attempt、新版本 |
| `select` | 不调用 | 新版本选择清单；使用现有产物，不称为重试 |
| `resume workflow` | 未完成节点执行；已固定节点只引用 | 新流程 run，引用旧的明确成功版本 |
| `recover publication` | 不重新算业务 | 修复已完成写盘产物的事务发布；保留旧执行身份 |

自动重试可限制次数，如可恢复网络错误最多 2 次、低质量译音最多 2 次，超过则 REVIEW/FAIL，防止无人值守无限花费。**次数限制不能把已决定执行的重试变成缓存返回。** 手动再次 retry 即使上次成功也允许。

一个 TTS 单元重试只重算该单元，不强制重跑其他单元。若选择新译音，重新执行依赖它的集合清单、时间线、字幕布局、合成及最终 QA；否则原成片仍引用旧版本，不因新文件出现被偷偷改变。重新选择 OCR 分支同理。

默认 retry 复用上次固定输入和有效参数，使用当前选定策略实现；新代码身份必须记录。要求“按当时环境复现”时，必须加载那一版代码/依赖/模型；找不到则失败，不假称复现。`retry --strategy B` 是同业务节点的另一实现，仍有 `retry_of` 和分支说明。

## 9. 日志设计

每个源视频目录一份主日志 `pipeline.jsonl`。所有节点通过控制面的统一日志写入器写入，模型 worker 不直接并发写主文件。保存节点原 stdout/stderr 属于执行证据；主日志记录其文件引用，不需要把大段二进制/Base64 塞入 JSONL。

```json
{
  "seq": 103,
  "utc_ns": 1780000000000000000,
  "asset_sha512": "<source>",
  "pipeline_run_id": "<run>",
  "attempt_id": "<attempt>",
  "node": "ocr",
  "strategy_id": "paddle_ocr",
  "git_commit": "<commit>",
  "event": "NODE_SUCCEEDED",
  "input_refs": ["<complete or referenced ArtifactRef list>"],
  "output_refs": ["<complete or referenced ArtifactRef list>"],
  "effective_params_ref": "nodes/ocr/all/v000003_<id>/request.json",
  "model_refs": ["<runtime model identity>"],
  "elapsed_s": 31.42,
  "status": "SUCCEEDED"
}
```

事件至少包括获取开始/结束/失败/归并、节点分配/开始/输入校验/实际调用、worker 启停、模型调用、产物验证、完成/失败/超时、重试请求、版本选择、最终交付与恢复。耗时使用 monotonic clock；时间戳使用 UTC；并发事件顺序用本地单调 seq 表示，不根据机器墙钟排序判断血缘。

开始事件保存完整有效输入和参数，结束事件保存完整输出清单或不可变清单引用。每条日志必须能追到节点策略与参数，不能只有“running OCR”“done”。多进程场景采用单写入器或锁+数据库 outbox；JSONL 是可从事件表重建的投影。日志文件损坏时保留损坏副本并重建，不能静默删掉错误历史。

## 10. 空档优先时间线的精确定义

### 10.1 逻辑算法

对于单元 i：`E_i` 是最后一个原始语音采样的结束边界，`S_(i+1)` 是下一受保护原始讲话开始，`C_i` 是经测量的译音时长。

```text
G_i = S_(i+1) - E_i
R_i = pre_pause + C_i + post_pause
H_i = max(0, R_i - G_i)
```

默认 `pre_pause=post_pause=1s`。中文开始于 `E_i + 已累计新增时长 + 1s`，自然空档内正常推进原片；只有原片即将到达下一讲话开始、但中文加后停顿尚未结束时，追加定格。

多个目标语言时按配置顺序播放：`英文→1s→中文→1s→日文→1s→下一英文`。对应 `R = pre + ΣC + (语言数-1)*between + post`。首版默认只输出中文，不为了多语言扩展改变基础数据契约。

空档不能为负。重叠讲话先在 `utterance_build` 归并或 REVIEW，不把负数直接截为零以掩盖问题。最后一句使用媒体结束作为边界；片尾足够就继续走，不足就延长最后一帧。空档比需要时间更长时不剪短。

### 10.2 视频帧和音频采样不在同一网格

工作帧率用有理数 `p/q`，如 `30000/1001`；工作音频采样率 r=48000。计划额外帧数：

```text
hold_frames_i = ceil(max(0, required_samples_i - gap_samples_i) * p / (r*q))
```

不逐段独立四舍五入额外音频长度。设此前累计定格帧数 D，则：

```text
offset_samples(D) = round_half_up(D * q * r / p)
hold_samples_i = offset_samples(D + hold_frames_i) - offset_samples(D)
```

这样累计新增音频相对于精确帧时长的误差最多约半个采样，不会随上千次定格累积。

### 10.3 音频切点在一帧内部时

原音按安全音频采样边界 S 暂停，不能为了对齐整帧把上一句切掉或先播出下一句。画面使用 S 前已经呈现的帧：

```text
video_insert_before_frame = ceil(S_samples * p / (r*q))
freeze_source_frame = video_insert_before_frame - 1
```

若 S 在帧中间，这一帧本来就在显示；延长其显示并在它之后插入整帧副本，下一原始帧按原有顺序继续。不要错误地对 S 向下取整后从那个切点截断音频。若 S 正好位于整帧边界，取前一帧；下一帧留到恢复时显示。冻结视觉检测区间允许包含被延长帧本来的显示时间，不能只按音频暂停起点对齐检测器。

### 10.4 已确定的例子

原视频 10 秒，25 fps、48 kHz。英文 A 为 2–5 秒，英文 B 为 8–9 秒。中文 A 4 秒、中文 B 1 秒。

| 阶段 | 原片时间 | 输出时间 | 内容 |
|---|---|---|---|
| 原片正常播放 | 0–8 | 0–8 | 英文 A 2–5；中文 A 从输出 6 秒开始 |
| 第一次定格 | 停在原片 8 秒前最后一帧 | 8–11 | 中文 A 继续到 10 秒，随后停 1 秒 |
| 原片恢复 | 8–10 | 11–13 | 英文 B 11–12；后停 1 秒 |
| 片尾定格 | 原片最后一帧 | 13–15 | 中文 B 13–14；末尾停 1 秒 |

因此输出 15 秒、375 帧、720000 个单声道 48 kHz 采样。前一版“每句完整追加定格”的原型不能沿用其算法；本包使用本节策略重新实现。

### 10.5 原音、背景声与字幕

原始语音及环境声使用同一源时间映射。正常推进原片且播放中文时，可按配置 duck 背景原音，但不得覆盖下一段原始讲话。定格期间原片音轨不前进，首版加入静音底；需要音乐连续时，另生成不含人声的背景延续片段并单独记录来源，不直接循环混有人声的原音。

中文 WAV 在正常播放→冻结→恢复的转换中必须连续，不拆成两次 TTS。原音保持顺序、不变速；原始片头、句间画面与片尾都保留。

中文显示期间保留对应英文/中文字幕，按实际朗读单元分页；不能在冻结时提前显示下一段字幕。长自然空档中，中文结束后的 1 秒可以淡出字幕，但不为了延长显示而继续覆盖下一段。每个字幕事件的时间都由新时间线生成。

FFmpeg 提供切片、PTS 调整、拼接与末帧延长机制；这些底层运算应直接复用，业务时间线仍由本项目生成。[^R06]

## 11. 字幕、修复、字体和模型适配细则

### 11.1 提取与修复

先保存字幕文本、坐标、显示区间和样式证据，再修复画面。低置信度 OCR 不阻止保留图像证据，也不能导致检测到的部分文字区域被当成不存在。区域检测、文本识别、重复事件合并是三个可独立替换的策略；首版可先用一个组合 worker，但输出保留三个层级的 raw 结果。

VSR 的区域检测、同掩膜区间划分和修复循环可参考；不能直接依赖其默认全屏处理和原音回填。VSE 有交互分支与固定临时目录，包装时必须移除/配置化这些行为。VideOCR 提供 CLI 和抽帧/合并相关参数，但应扩展输出位置、置信度与证据，不只保留 SRT。[^R02][^R03][^R04]

修复输出要验证帧数、尺寸、时钟及与原帧的映射。修复模型失误不能以“程序退出码为 0”判通过。非掩膜区域的比较要考虑编码损失；优先在无损工作帧上比较，不把有损重编码造成的微小差异都当成误擦。

### 11.2 字体与排版

有 ASS 样式时读取原字体元数据，但仍检查实际可用字体与中文字形。只有像素时，用已许可候选字体库和字幕多帧证据匹配。YuzuMarker.FontDetection 可提供字体候选，不等于唯一确定原字体；fontTools 可检查字形映射，pysubs2/libass 可承担字幕数据和渲染。[^R07][^R08][^R09][^R10]

新开发内容包括颜色/字重/描边/阴影估计、候选字体回渲染比较、中文字形覆盖、双语布局、换行和实际占用边界检查。`font_match_status` 为 EXACT_METADATA / APPROXIMATE / UNRESOLVED；没有原字体证据时不能标 EXACT。

排版规则版本独立于识别模型。采用两个独立 ASS 样式处理英文/中文，而不是强迫整个双语串使用只有拉丁字形的字体。适当上移英文；超出布局区域先换行再有限缩小字号，仍不满足就 REVIEW。校验输出截图中的字形，不仅检查配置数值。

### 11.3 翻译与语音模型

翻译输入是固定 `UtteranceSet` 版本、上下文和术语表；返回相同 ID 的译文。ID 丢失、重复、原文变更、额外生成没有请求的语句均失败。没有原时长压缩要求，不做为了“塞回原时间窗”的删词。

TTS 输入明确包括参考音频 ArtifactRef、准确参考转写、目标语言、spoken_text、音色/情绪参数和模型配置。不同单元可以选择同一个已验证说话者参考，但每次生成都是新的模型调用版本。只说一个词的原始片段不强制用作唯一参考。

模型音频生成后独立测量实际采样数与发声范围；不能信任模型返回的宣称时长。中文生成失败不能静默替换为普通音色然后继续声称克隆成功。每一次降级必须是明确策略、新版本及质量标记。

## 12. 下载策略与环境检查

直接依赖 yt-dlp，不复制 YouTube 提取器。采用独占输出目录、`--ignore-config`、单视频模式及结构化获取最终文件路径。保存实际格式 ID、音轨语言/来源、容器、编码、分辨率、yt-dlp/FFmpeg 版本和获取日志。当前官方文档要求完整 YouTube 支持需考虑 `yt-dlp-ejs` 与受支持的 JavaScript runtime，不能仅验证 Python 包是否安装。[^R01][^R15]

默认 URL 中 `t=33s` 等只是网页播放位置，不截去前 33 秒；完整下载并记录这个选择。拒绝 playlist-only URL、未结束直播、需要未提供授权的访问，不尝试绕过 DRM。下载与音色克隆用于有权处理的素材，不自动发布或冒充原说话者。

`bv*+ba/b` 是骨架的通用格式选择；生产英文源策略必须验证选中的音轨是期望的英文原音，而不是网站提供的自动配音轨。首轮可读取格式信息后固定格式 ID；无法确认原音语言时 REVIEW，不静默下载其他语种继续处理。

yt-dlp 更新应通过受控的 worker/依赖锁版本升级，升级后的重试保留新版本信息。不要在已开始的 attempt 中自动升级依赖。下载失败和源内容变化不应污染已经建立的 SHA 目录。

## 13. 自动质量检查与失败处理

| 检查层 | 必做检查 | 判定方式 |
|---|---|---|
| 源/工作媒体 | 所需流、PTS/帧率、声道、时长、完整解码 | 确定性校验；异常显式 FAIL/REVIEW |
| 字幕提取 | OCR 漏检/误识别、时间区间、重复事件、非字幕文字混入 | 对照证据与独立检测；保存低置信度定位 |
| 清理视频 | 残字、非目标大面积改动、修复时序异常 | 在 clean video 上检查，不能混入新字幕 |
| 翻译 | 数字、单位、专名、否定词、漏译、错误 ID | 规则与模型审核；保存问题单元 |
| 译音 | 无效/空音频、语言、漏词、多词、重复、音色突变、削波 | 独立 ASR + 音频规则 + 可选说话者比较 |
| 时间线 | 源内容恰好保留一份、只有计划中的新增帧、没有语音碰撞 | 帧数/样本数/映射不变量 |
| 排版 | 字形缺失、双语重叠、越界、下一字幕提前 | 实际渲染边界与截图 |
| 成片 | 完整解码、结构时长、音画同步、意外冻结/静音 | 对照计划与原片静态区间；检测器只提供证据 |

最终 `PASS` 要求所有 mandatory 检查实际通过；`REVIEW` 表示已经生成但有未解决的不确定；`FAIL` 表示关键结构/内容错误；`SKIPPED` 只用于具体检查项未执行，不能升级为通过。程序“成功生成文件”和“内容质量可交付”是两个状态。

QCTools/qcli 可提供额外媒体统计报告，不能替代本项目的业务门禁。[^R11] 检测器发现的冻结必须扣除计划定格及原片原本静止的区间。可接受的阈值在固定验证集上标定并版本化，不把凭经验的数字当作已验证准确率。

日志和 QA 证据必须同时给出源时间、输出时间和受影响的 attempt/artifact，便于自动定位失败节点重试。视觉模型可作为补充审查，不让它单独决定全片没有任何错误。

## 14. 测试与验收

### 14.1 已随包执行的测试

实际清单以 `verification/TEST_REPORT_ZH.md` 和 JUnit 文件为准。单测覆盖：SHA 归并/变更、同参强制重试、旧产物不被覆盖、策略替换、精确血缘、错误/缺失/跨源产物、版本并发、日志重建、路径限制、秘密参数、时间线边界、多语言、无语音、重叠拒绝及 29.97 fps 累计舍入。

真实 FFmpeg 集成使用合成图案视频和不同频率提示音，验证“空档充足零定格”和“空档不足补 3 秒、片尾补 2 秒”。参考答案独立按场景手工构造，不直接把规划器结果重新当金标准；逐帧比较无损主视频，逐采样比较无损主音轨，再完整解码 H.264/AAC 成片。

这些测试不说明真实英文/中文语音、OCR 或去字幕质量已经通过。联网下载和真实模型测试有 opt-in 条件，没有环境就 SKIPPED。

### 14.2 真实环境测试分层

完整场景、准备方法、运行步骤与验收结果见 `REAL_TEST_MATRIX_ZH.md`。至少包含：

- 自有/授权的 YouTube 测试视频：真实下载、音轨验证、失败重试、重新获取、迁移、同源不同格式。
- 已知干净原片 + 人工烧录英文字幕：两个 OCR 策略对照，保留场景文字，清理后与原始干净视频比较。
- 授权录制的英文单人/双人语音：长空档、短空档、无空档、句尾气声、背景音乐、重叠讲话、无字幕讲话。
- 模型真实生成：单词/短句/数字/专名、多语言、相同输入重复执行、提供者超时或返回不完整音频。
- 真实媒体边界：VFR、30000/1001、非零 PTS、旋转、音频偏移、片尾无空档、损坏媒体、缺音轨。
- 系统故障：worker 被杀、主进程被杀、磁盘满、日志半行、输出篡改、并发同源任务、历史版本回放。

只在 pytest 输出中出现一个 skipped 测试，不能等同于整个真实场景矩阵已经有自动化实现。生产 CI 应区分 unit、local-real-media、network、models、fault-injection 和 release-gate 六类报告，列出计划/已实现/已执行状态。

## 15. 开发分期与可交付完成定义

| 阶段 | 应实现内容 | 可验证的完成标准 |
|---|---|---|
| P0 存储与执行内核 | 内容身份、attempt、血缘、插件、强制重试、汇总日志、事务恢复 | 相同请求执行 3 次产生 3 个新版本；崩溃不能发布半成品；任何文件可追到输入与代码 |
| P1 真实媒体与时间线 | 下载、归一化、时钟、真实译音长度输入、空档优先合成 | 实际示例与随机案例满足不变量；长视频无累计漂移 |
| P2 字幕与语音证据 | 字幕/OCR A/B、全部讲话检测、稳定单元映射 | 同一固定数据集输出一致契约；未字幕化讲话不会被中文覆盖 |
| P3 清理与样式 | 目标掩膜、VSR 接入、字体候选、双语布局 | 保留场景文字；清理质量报告可定位；缺字/越界可检测 |
| P4 模型节点 | 翻译、参考音频、克隆 TTS、音频标准化和单段 QA | 新重试实际调用；请求响应完整留存；新长度触发新时间线 |
| P5 无人值守门禁 | 全片 QA、重试策略、分支选择、发布、恢复与资源约束 | 零人工操作跑完授权集；通过/需复核/失败分类正确，不虚假 PASS |

不得先做 GUI 再补不可变数据协议。优先完成 P0/P1，随后用同一标准 schema 接入各类 worker。首版不需要自主 agent 循环；固定 DAG + 可替换策略更容易证明重试、血缘和时间线正确。

## 16. 参考骨架的使用

详见根目录 `README_ZH.md`。以下命令已与当前 CLI 保持一致：

```bash
# 无需安装本项目也可通过 PYTHONPATH 执行
PYTHONPATH=src python -m bivideo.cli demo --out /tmp/bivideo-demo-short
PYTHONPATH=src python -m bivideo.cli demo --out /tmp/bivideo-demo-long --long-gap
python -m pytest -q

# 导入本地视频并得到 source_ref
PYTHONPATH=src python -m bivideo.cli --data ./run-data ingest --local /path/to/video.mp4

# 在输出的 SHA-512 上运行真实 ffprobe
PYTHONPATH=src python -m bivideo.cli --data ./run-data node-run \
  --asset "$ASSET_SHA512" --strategy ffprobe

# 同参数强制再执行；会生成新的 attempt
PYTHONPATH=src python -m bivideo.cli --data ./run-data retry \
  --asset "$ASSET_SHA512" --attempt "$ATTEMPT_ID"
```

项目没有实现一个假装能完成全部 AI 节点的 `run-full` 命令；完整 DAG 调度器及各实际 worker 需按本设计开发。`fixture_a/fixture_b/fixture_tones` 的命名明确表示测试，不能配置为生产 OCR/TTS 策略。

## 17. 上游源码版本和复用边界

完整定位见 `CODE_REFERENCES_ZH.md`。在线核查的上游文件位于公开 main/master；这些分支会变化，本包没有伪造对应 commit。`references.catalog.json` 将需要锁定的 commit 保留为空，`scripts/pin_upstreams.py` 在开发环境解析并验证固定 commit，之后将锁文件提交到项目。正式运行必须使用已解析 commit/依赖/权重锁，不能把这份候选清单直接当成发布锁。

本项目自己的构建 commit 和代码树摘要由 `BUILD_INFO.json`、`SOURCE.bundle` 及每次执行的 code provenance 记录。开发模式允许 dirty 但如实标注；生产严格模式应拒绝未提交或没有可确认身份的代码。第三方代码直接复制前检查许可证、保留版权和改动记录；独立进程仅提供依赖隔离，不自动豁免许可证义务。ProPainter 的许可包含非商业限制，应单独核查，不因外层集成项目开源就假定可商用。[^R16]

## 18. 必须新开发的最终清单

核心新开发是：内容身份与获取归并、不可变执行/文件版本、精确血缘与选择快照、无缓存重试、标准契约和 worker 协议、字幕/语音关联、说话者参考选取、原字体近似匹配与中文映射、空档优先时间线、双语自动布局、业务 QA 和故障恢复。

直接依赖或适配：yt-dlp、FFmpeg/FFprobe、pysubs2/libass、fontTools；参考包装 VideOCR/VSE/VSR/WhisperX。pyVideoTrans 只参考字幕排列代码；不继承其主任务状态、基于列表下标关联双语字幕和替换式配音的时间线。[^R12]

整个设计的验收基准不是“所有进程退出码都为 0”，而是：**任意产物能追溯，任意重试确实执行，任意策略可替换，原始内容不丢失，新增时间恰好可解释，未验证能力不会被标成成功。**

## 来源

[^R01]: yt-dlp 官方仓库，README 与 `yt_dlp/YoutubeDL.py`。https://github.com/yt-dlp/yt-dlp ，https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py 。
[^R02]: VSR，`backend/main.py`、`backend/tools/subtitle_detect.py`。https://github.com/YaoFANGUK/video-subtitle-remover 。
[^R03]: VSE，`backend/main.py`。https://github.com/YaoFANGUK/video-subtitle-extractor/blob/main/backend/main.py 。
[^R04]: VideOCR，`CLI/videocr_cli.py`。https://github.com/timminator/VideOCR/blob/master/CLI/videocr_cli.py 。
[^R05]: WhisperX，`whisperx/alignment.py` 及项目限制。https://github.com/m-bain/whisperX ，https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py 。
[^R06]: FFmpeg Filters 官方文档与 `libavfilter/vf_tpad.c`。https://ffmpeg.org/ffmpeg-filters.html ，https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/vf_tpad.c 。
[^R07]: YuzuMarker.FontDetection，`demo.py`。https://github.com/JeffersonQin/YuzuMarker.FontDetection/blob/master/demo.py 。
[^R08]: fontTools `TTFont.getBestCmap()` 官方文档。https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html 。
[^R09]: pysubs2 API 与 `pysubs2/ssafile.py`。https://pysubs2.readthedocs.io/en/latest/api-reference.html ，https://github.com/tkarabela/pysubs2/blob/master/pysubs2/ssafile.py 。
[^R10]: libass，`libass/ass_render.c`。https://github.com/libass/libass/blob/master/libass/ass_render.c 。
[^R11]: QCTools 官方 README 的 using qcli 部分。https://github.com/bavc/qctools#using-qcli 。
[^R12]: pyVideoTrans，`videotrans/task/_stage_subtitle.py`。https://github.com/jianchang512/pyvideotrans/blob/main/videotrans/task/_stage_subtitle.py 。
[^R13]: Python 官方文档，`importlib.metadata`。https://docs.python.org/3/library/importlib.metadata.html 。
[^R14]: SQLite 官方文档，Write-Ahead Logging。https://sqlite.org/wal.html 。
[^R15]: yt-dlp 官方 EJS 配置说明。https://github.com/yt-dlp/yt-dlp/wiki/EJS 。
[^R16]: ProPainter LICENSE。https://github.com/sczhou/ProPainter/blob/main/LICENSE 。
