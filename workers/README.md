# Worker 协议

worker 入口为 `python -m mlvideo.worker --request REQUEST --result RESULT`，由主控通过进程组启动闸门调用。worker 不读取数据库配置，不接收数据库凭据。N11 由该入口在同一进程组内启动独立 Python 环境中的 `cosyvoice_worker.py`；父进程超时/退出会回收该进程组。

request 含 `protocol_version=1`、asset/run/execution、node/strategy/version、固定输入 `port -> [ArtifactRef]`、params、models 和 output_dir。result 必须符合 `schemas/WorkerResult.v1.json`。产物路径相对本次 work；禁止绝对路径、`..` 和所有符号链接。端口/schema 由 `contracts.STRATEGIES` 固定，主控验证后复制到 artifacts 并封存。

`TEST` 策略明确为 fixture，不代表模型推理。`MLVIDEO_TEST_FAULT` 只供验收主控注入中断，不能通过配方配置。`fill_bytes` 只用于 TEST 策略的受限磁盘验收。

## 第二期首批节点

- `N11/cosyvoice3_zero_shot`：固定 translation、reference、audio 三个输入 artifact。按 `params.unit_id` 选文本；参考转录与音频哈希/区间一致才执行。保留全部生成块和原始 FLOAT WAV，输出 RawDubClip、实际模型回执与模型源码 ZIP。
- `N12/audio_qa`：输入原始 audio 和 RawDubClip；核对哈希、采样率、声道与采样数，输出完整的 48 kHz/双声道/PCM16 副本、DubClip 和 AudioQA。20 ms RMS 能量法仅供前导异常筛查，默认持续显著能量出现晚于 1 秒即 REVIEW；不自动裁剪。内容、尾音和人工听感未验证时不产生 PASS。
- 静音或削波的质量 FAIL 写入 execution 的质量状态，并阻止下游使用。模型部署信息随 request 固定；retry 沿用原声明，重新执行，不用当前配置替换旧模型路径。

模型部署只从本地配置进入 request，不能作为配方参数传入执行命令。在 `--config` 指定的私有配置中添加：

```json
{
  "models": {
    "N11/cosyvoice3_zero_shot": [{
      "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
      "revision": "29e01c4e8d000f4bcd70751be16fa94bf3d85a18",
      "python": "/absolute/path/to/cosy-env/bin/python",
      "source_dir": "/absolute/path/to/CosyVoice",
      "model_dir": "/absolute/path/to/Fun-CosyVoice3-0.5B-2512"
    }]
  }
}
```

这段配置需合并到已有数据库/data_root 配置，不是完整配置文件。独立环境依赖及实际版本见模型回执；本机复用比较阶段的部署。revision 来自本地 HF 下载 metadata，全量权重哈希未在调用内计算，回执保留 null 和原因。源码 ZIP 与依赖版本不能替代完整权重验证。

`TranslationSet.v1` 是当前 N11 使用的批次文本输入契约；N09 真正调用、原始响应及语义验证尚待实现。使用手工输入做 worker 验证时，必须标注其来源，不能声称真实翻译完成。第一期 N16/N18 仍为提示音示例策略，尚未接入这些译音产物。

真实协议冒烟命令与结果见 [N11/N12 验证记录](../docs/verification/PHASE_2_VOICE_WORKERS_ZH.md)。

## 第二期候选链路（2026-09-12 增补）

本节替代上文“尚待实现”的当前状态描述；首批 N11/N12 的历史运行证据保持不变。

| 策略 | 作用与边界 |
|---|---|
| `N05/inventory` | 原始 probe 清点软字幕，保存规范化视频截图与 ROI；硬字幕存在性保持 unknown，不能由单帧断言不存在 |
| `N06/captions` | soft 模式提取唯一软字幕轨并转换时间基；ocr 模式调用真实 PaddleOCR，保留请求、原始响应、整帧坐标及抽帧。只按 ROI 筛选候选行，不自动认定文字就是字幕 |
| `N07/whisper` | Whisper small 英语 ASR/词时间 + 独立 Silero VAD；保存权重哈希与原始结果。未确认覆盖时保护整个源 PCM；自动 diarization 尚未部署 |
| `N08/group` | ASR 文本归句，保留 speech/caption 多对多引用，合并交叠或无安全整帧切点的同一说话者片段；不同说话者无法安全合并时拒绝 |
| `N09/codex` | 每个 execution 一个完整批次；最多 8 句、6,000 Unicode 字符。固定 `gpt-5.6-terra / medium`，保存 prompt、schema 哈希、JSONL、原始响应和回执；严格校验 ID，不复用结果 |
| `N10/reference` | 按明确 speaker、区间与转录从源 PCM 提取完整参考音；1–30 秒，保留原区间与哈希。参考转录准确性仍需人工确认 |
| `N12/audio_qa_asr` | 保留原 `audio_qa` 的完整 PCM 转换与前导/削波检查，额外真实执行 Whisper 中文转录，完整保存 ASR 结果与辅助 CER；不据此宣称语义/听感通过 |
| `N16/dub_gap_first` | 使用实测 DubClip 长度；核对 raw clip、译文、speaker、unit ID 与 artifact 引用后规划时间线 |
| `N17/bilingual` | 固定字体文件哈希，检查字形覆盖和像素边界，生成英上中下透明字幕帧及布局。超出下半屏则拒绝，需重新归句或明确调整字号 |
| `N18/dub_ffmpeg` | 插入完整真实译音，按输出时间叠加字幕，包括定格帧；母版 FFV1/PCM 与 MP4 预览均解码检查 |
| `N19/review` | QA 必须来自同一次 render，且绑定布局与每条原始译音；缺少人工决定为 REVIEW。错译/音频 FAIL 不可变成 PASS |

`N16/gap_first`、`N18/ffmpeg` 仍是第一期提示音策略；第二期使用不同策略 ID，不改变旧配方含义。

### 本地模型配置

除上述 CosyVoice 部署外，在完整本地配置的 `models` 添加以下对象。路径和哈希须替换为实际部署；密钥不进入文件。

```json
{
  "N06/captions": [{"endpoint": "http://127.0.0.1:58081/ocr", "model_id": "PaddleOCR", "revision": null, "unknown_reason": "服务未回报模型版本"}],
  "N07/whisper": [{"python": "/absolute/model-env/bin/python", "model_path": "/absolute/whisper/small.pt", "sha512": "实际权重SHA512", "vad_package_dir": "/absolute/speech-deps", "vad_sha512": "silero_vad/data/silero_vad.onnx实际SHA512"}],
  "N09/codex": [{"executable": "codex", "model_id": "gpt-5.6-terra", "effort": "medium"}],
  "N12/audio_qa_asr": [{"python": "/absolute/model-env/bin/python", "model_path": "/absolute/whisper/small.pt", "sha512": "实际权重SHA512"}],
  "N17/bilingual": [{"font_path": "/absolute/font.ttf", "sha512": "实际字体SHA512", "source": "字体来源与使用说明"}]
}
```

本机使用 `config/local-phase2-001.json`，沿用第一期隔离验收 MySQL/data_root。Silero VAD 6.2.1 额外安装在挂载盘 `speech-deps`，未改动此前 CosyVoice 环境的包集合。ASR 与 TTS 同样通过独立进程执行，并随主控超时回收；worker 不直接写数据库。

### 执行与重试

先 `ingest` 获取 `asset_sha512`、`source_artifact_id`，再运行：

```bash
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json candidate \
  ASSET_SHA512 --source SOURCE_ARTIFACT_ID --settings config/local-candidate-settings.json
```

settings 包含 `caption_mode`（ocr/soft）、`stride_frames`、`roi`、`font_size` 与 `references`。参考音示例：

```json
{
  "caption_mode": "ocr", "stride_frames": 25, "roi": [0, 0.5, 1, 1], "font_size": 24,
  "declared_single_speaker": "speaker1",
  "references": [{"speaker_id": "speaker1", "start_sample": 0, "end_sample": 240000, "transcript": "Exact English reference transcript."}]
}
```

`declared_single_speaker` 是调用者对素材的明确声明，不是自动说话者识别结果；不得对未知多人素材填写它。确认过的多人素材可提供 `speech_annotation`：包含 `audio_sha512`（规范化 PCM 文件）、`reviewer` 和 `segments`（每项 start_sample/end_sample/speaker_id/text）。标注不能交叠、越界或遗漏 ASR/VAD 检测到的人声。未提供标注时整段保护，可能无法得到逐句短片或在长片上触发超长归句 REVIEW。

每次候选运行登记新的 run，按批次/句子分别登记 execution。`retry ASSET EXECUTION` 保留所有输入端口的完整 artifact 列表及原模型声明，重新启动真实 worker。失败节点的 request、日志及已完成上游保持可查。

N19 的 `human_decision` 参数可提交符合 `ReviewDecision.v1` 的人工记录。必须指定确切 render artifact/hash、reviewer，并逐一覆盖本次全部待确认项；错误绑定、缺项或总体决定与分项矛盾均拒绝。它是用户提供的复核记录，不能由程序虚构 reviewer 或自动填写 PASS；不修改此前 execution。

第二期验收及未解决项见 [整体验证记录](../docs/verification/PHASE_2_FULL_VERIFICATION_ZH.md)。


## 用户原片硬字幕预览

`N04/excerpt` 在同一原片 asset 下生成指定区间的规范化预览，输入为原 source/probe，参数为 start_seconds/end_seconds/height，输出额外的 excerpt 回执。`candidate` settings 可通过 `excerpt` 选择这一策略。

`N08/group` 的 `text_source=caption_consensus` 适用于单个完整硬字幕页的短预览：按帧和文字框顺序合并行，非空白字符跨帧一致才采用；空格形式采用出现次数最多的真实读数，平票拒绝，且保留 REVIEW 和全部原始结果。默认 `speech` 不变。它并不自动实现全片多页字幕与语音的归句。

`N17/bilingual` 可设置 `preserve_source_english=true` 与 `footer_height`，只在扩展的底部区域绘制中文，原画面中的英文保持不变。字号和底部高度仍经过容量与像素边界检查。

OCR 私有部署可固定 `request_options` 中的 textDetLimitSideLen、textDetLimitType、textDetUnclipRatio；不允许覆盖 file 或 endpoint。此次实际原片参数与失败记录见 [YouTube 原片预览报告](../docs/verification/YOUTUBE_Ye33eY4UNtY_PREVIEW_ZH.md)。

## 当前字幕规则与全片参考库

按用户后续要求，硬字幕预览默认 `footer_height=0`：中文字幕在原画面内，优先放在英文字幕正下方；下方空间不足时自动移到英文上方，保持原视频尺寸，不添加黑边。`candidate` 在未显式给定 `source_subtitle_box` 时从当前 OCR 框取得边界；放不下或会覆盖英文则拒绝，不裁切文字。历史 footer=144 运行保留，仅用于复查旧结果。

新增 `N04/audio_source` 保留整条视频的 FLOAT 解码音频，并测量峰值后在必要时降幅转换为 PCM16 参考副本，避免浮点溢出在整数转换中削波；实际增益保存在回执中。`N07/speaker_diarization` 使用独立 Sherpa-ONNX 环境，当前公开模型组件及其精度核对见 [模型方案与 Mac 基准](../docs/verification/COSYVOICE_MAC_AND_SPEAKER_OPTIONS_ZH.md)。

`N10/speaker_bank` 按声学分组收集参考候选，保存每段原音频区间与 ASR 转录。优先连续片段，缺少足够长的连续片段时可构建带明确间隔的候选池；`VoiceReference.v2` 记录全部来源区间、间隔样本数和最终采样数。`N10/from_speaker_bank` 重新构建并核对候选哈希；参考不足不能克隆。`N11/cosyvoice3_from_bank` 消费该 v2 引用并执行真正的原版 zero-shot 合成。

### 说话人与参考音复核

`N08/reviewed_speech` 输入 `audio`、`speech`、`speakers`、`vad` 四个 artifact。`decision` 包含四个 `<port>_sha512`、具名 `reviewer`、完整 `segments`（start_sample/end_sample/speaker_id/text）和三项 checks：`speaker_identity`、`speech_boundaries`、`transcript`；每项必须有 PASS 与具体 reason。必须保护 ASR、VAD、diarization 检测到的全部人声。原始估计保留，新执行返回 `speech`、`speakers`、`review`，再用这两个已复核分析输出构建新的 N10 bank。

`candidate` 可提供 `speaker_bank` 对象，包含 `bank`、`speech`、`review` artifact ID，以及 `references` 数组；speech/review 必须来自同一次 N08 reviewed_speech，bank 必须使用该次 speech/speakers。源音频必须与本轮规范化 PCM 哈希完全一致。每条 references 包含 speaker_id、candidate_id、review；review 必须绑定 bank_sha512、candidate_id、speaker_id、audio_sha512，具名确认 `speaker_identity`、`transcript`、`clean_reference`、`complete_words` 四项。候选流程会在翻译/合成前验证参考音。既有 `references` 的手动短预览路径保留。

用 `scripts/prepare_speaker_review.py --config CONFIG --asset SHA512 --analysis ANALYSIS_RESULT_JSON --diarization DIARIZATION_RESULT_JSON --bank BANK_RESULT_JSON --out NEW_DIRECTORY` 导出分组试听包及未批准的 JSON 模板。analysis 文件包含 audio 和 speech 两个 engine 结果；其他两个文件分别为相应 engine 结果。所有模板仅供实际复核，不能自动填写 PASS。

N19 每条音频新增 `voice_identity_i`、`naturalness_i`、`leading_noise_i`、`tail_integrity_i`、`spoken_content_i`。旧三段预览方向的确认不能替代新译音/整片的这些具体决定。当前执行边界见 [收尾报告](../docs/verification/PHASE_2_CLOSEOUT_ZH.md)。

这些新接口的协议/采样检查不代表声学分组可靠性已经通过。当前动画全片的分组仍随阈值和预处理变化，参考音库保持 REVIEW 草稿，不能自动当作已确认人物音库。CosyVoice3 主干及原版前端精度已核对，不是 MLX 8-bit；Qwen MLX 8-bit 不再作为当前选型重点。


## 多页硬字幕显示时序

`N08/caption_pages` 将有源帧证据的字幕页面与 ASR/VAD 关联，输出页面始末帧、文字框和受保护语音区间。`N16/dub_gap_first` 增加字幕可见性切点约束，`N17` 将源字幕区间映射到含定格的输出时间。中文按原英文页面显示时段保持，不在译音刚结束时提前隐藏。

配方支持多输入端口的固定 artifact ID 数组；端口数量、去重和 schema 在执行前检查。当前三段预览的源页边界由助手查看实际帧后固定，未声称完整视频已实现无人复核的自动分页面验收。
