# 数据契约与生产校验约束

> 历史参考，2026-09-11 补充说明：本文原文来自 [video_interleave_v2_design_and_skeleton.zip](../sources/video_interleave_v2_design_and_skeleton.zip)，内部路径相对于包内 `video_interleave_v2/`。实现/测试状态均指该参考包，未在本次更新中重跑；当前 MySQL 串行方案见 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和 [文档入口](README_ZH.md)。下方保留原始内容。

本文件是待接入业务节点的实施规格。`vidflow/contracts.py`是骨架已运行的契约子集；这里列出的全部业务契约并未全部编码/接入。新增节点前先为对应类型实现JSON Schema及语义validator，禁止用任意dict直接返回success。

## 1. 公共约定

每个输出文件由执行manifest确定asset、producer、code、models、params、parents。业务JSON不需要重复全部执行元数据，但需要明确schema版本及所有复合业务ID。下游引用JSON内同执行的媒体文件时，通过`execution_id + relative_name`解析为artifact，不按当前工作目录或全局“最新”寻找。

文件路径必须为本次artifact根下的相对路径，不允许`..`、绝对路径、symlink或未知外部URL。远程模型返回的音频必须先下载进入本次产物并计算哈希，才能成为DubClip；不能只保存一个即将过期的URL。

时间域至少区分：source_container_pts、canonical_source、output_timeline。schema声明本类型使用哪一个。frame index从0开始；区间左闭右开。音频单位为sample frame（双声道同一时刻的两个采样算一个sample frame），不是字节数或左右声道相加。秒数精确字段使用有理数字符串`123/25`或整数微秒，不使用浮点作为唯一真值。

每个校验失败输出错误类别、字段路径、可定位证据，而不是只返回“系统异常”。unknown、not_applicable与SKIPPED不是相同语义。

## 2. 各类型字段与不变量

### SourceAsset / MediaProbe / CanonicalMedia

- SourceAsset：asset_sha512、source_path、source_bytes、source_name/origin收据引用；全文件hash匹配。
- MediaProbe：原始ffprobe JSON保留；适配后的streams包括index/type/codec、time_base、start_pts、duration、rotation、color、audio layout、subtitle codec。
- CanonicalMedia：source_asset、video_ref、original_pcm_ref、width/height、fps_num/fps_den、source_frames、sample_rate、source_samples、canonical_origin_in_source、time_map_ref、normalization_policy、unsupported_features。
- TimeMap：源PTS→工作帧/样本区间；容器偏移、丢帧/重复帧原因和策略。所有音画处理使用同一映射，不独立把两个流都拉回0。
- 校验：A/V起点和尾部对齐误差受明确定义的容差约束；宽高>0；fps>0；帧和采样数从实际解码统计。原视频无语音不是无音轨；可用策略需明确。

### SubtitleInventory / ROITrack / CaptionTrack

- Inventory：soft/hard/mixed/none，字幕流/旁挂来源及下载收据，language、自动转写标识、与画面关联置信度。
- ROITrack：canonical_video_ref，regions[{start_frame,end_frame,polygon/box,label,confidence,evidence}]；label区分dialogue_subtitle、title、overlay_text、unknown。
- CaptionTrack：见主设计captions.v1。生产增加的多边形/单字结果留raw或升级契约，不能偷偷改变bbox含义。
- 校验：start<end，bbox落在工作画面，转换坐标有迹可循；字幕可重叠，不应错误要求所有cue互斥；同一ID只能一次出现。ASR纠正文案必须新产物并保存原文。
- OCR空结果仅在确认无目标字幕时可NOT_APPLICABLE；对于已知有硬字幕的素材，应REVIEW/FAIL。

### SpeechTrack

```text
canonical_audio_ref
sample_rate
protected_speech: [{start_sample,end_sample,speaker_ids,confidence,source}]
words: [{text,start_sample,end_sample,speaker_id,alignment_status}]
speakers: [{speaker_id,embedding_ref?,confidence}]
overlap_regions: [{start_sample,end_sample,speaker_ids}]
failures: [{range,reason,fallback_used}]
```

protected_speech包括未显示字幕的讲话。词级对齐失败退回原段时间时必须标记fallback。重叠语音可多个speaker；不能强行指派一个speaker掩盖不确定性。采样区间落在canonical音频内。

### UtteranceSet

```text
caption_track_ref / speech_track_ref / canonical_media_ref
utterances: [{id,caption_cue_ids,speaker_id,source_text,
              start_sample,end_sample,safe_cut_frame,
              safety_evidence,grouping_reason,status}]
```

本节点将视觉caption片段与语音语义单位分开。ID作用域是本UtteranceSet。安全切点不早于本单元最后语音结束，且不晚于下一保护语音开始；无安全切点必须重分组，不允许负gap强行归零。词不被重复或遗漏；没有字幕的原讲话也要么成为单元，要么进入明确不翻译但受保护区域。

### StyleProfile

```text
style_id / evidence_caption_refs
font_candidates: [{name,local_font_ref,file_hash,score,method}]
selected_font_en / selected_font_target
font_match: EXACT_METADATA | APPROXIMATE | UNKNOWN
size_px/relative_height / weight / fill_rgba / outline_px/rgba
shadow_offset / shadow_rgba / alignment / anchor_xy
coverage_check: {language,missing_codepoints,status}
```

一个视频可多种样式。RGBA和ASS色码转换必须有测试。元数据证明字体与视觉匹配置信度分开。字体文件属于部署资源，不随意打包传播；manifest中保留hash与授权来源。

### MaskTrack / CleanVideo

- MaskTrack：canonical_video_ref、mask_shape、coordinate_space、ranges[{start_frame,end_frame,mask_file,roi_ids}]、dilation/interpolation参数、scenes引用。
- CleanVideo：canonical_video_ref、mask_track_ref、clean_video_file、实际frames/fps/size/PTS检查、algorithm/model_receipt_ref、changed_regions/evidence、quality状态。
- 校验：mask尺寸相符；mask不得出ROI；帧索引统一；输出不改变时间线。合法无硬字幕时可由passthrough策略产出标准CleanVideo，但该节点仍实际执行校验并记录新版本，不能隐式跳过。

### TranslationSet

```text
utterance_set_ref / target_language / glossary_ref / context_refs
items: [{utterance_id,source_text,display_text,spoken_text,
         entities,numbers,terms_applied,qa_status,warnings}]
model_receipt_ref
```

LLM不能改utterance_id、源时间和说话者绑定。display_text保留正常书写；spoken_text用于明确数字/缩写读法。输入ID集合与输出ID集合相等，不按返回数组下标匹配。空译文、重复ID、未知ID是协议失败。批量翻译一次得到多条时保留整批请求响应血缘，各条产物指向同一调用证据。

### VoiceReference / RawDubClip / DubClip

- VoiceReference：speech_track_ref、speaker_id、选取区间及scene、reference_audio_file/hash、准确transcript、质量/授权ref；多个片段必须同speaker。
- RawDubClip：复合utterance/speaker/language身份、translation_ref、voice_reference_ref、provider音频原始文件、model_receipt_ref。
- DubClip：raw_clip_ref、标准PCM file、sample_rate/channels/format/sample_count、speech_first_sample/speech_last_sample、normalization_policy、trimmed_ranges、contentQA。
- 音频长度从实际解码测量，不用模型说的duration；leading/trailing silence另存，不裁语音。保存原始响应以便重验。

### ModelReceipt

```text
provider / requested_model / resolved_model / model_revision
weights_sha512? / tokenizer_revision? / adapter_deployment_ref
request_id / execution_id / actual_parameters / seed
prompt_file / prompt_sha512 / response_file / response_sha512
reference_audio_refs / languages / device / quantization
started_at / ended_at / latency / usage / billed_units?
provider_cache_behavior: DISABLED_CONFIRMED | UNKNOWN | HIT
reproducibility: FULL_LOCAL_METADATA | PARTIAL_PROVIDER_OPAQUE
```

不公开revision的API字段为null并说明，不能写虚假的固定commit。未执行模型不能返回真实模型回执；mock必须标记fixture且不能通过生产QA门禁。

### TimelinePlan

骨架已实现的schema位于contracts.py，时间规则位于timeline.py。piece kind为source或hold；source范围整段覆盖原视频，hold引用上一帧；每段译音保存绝对输出起点、samples、输入audio role、utterance id。hold frame数是整数，中文起点按音频sample对齐。

生产新增canonical/utterance_set引用、all_speech_protection验证结果、policy_id、frame/sample舍入证据。多语言集合默认每语言独立TimelinePlan，禁止同一name文件覆盖。

### SubtitleLayout / AudioMaster / RenderBundle / QAReport / Release

- SubtitleLayout：ASS file、style_ref、timeline_ref、font清单、events[{utterance_id,language,start/end,actual_bbox,overflow}]、布局policy。
- AudioMaster：timeline_ref、各原音/译音input refs、PCM file/实际采样数/响度/峰值、混音policy及静音/原音暂停区间。
- RenderBundle：timeline_ref、cleanvideo/ASS/audio refs、输出预览/母版文件、编码器完整参数、frame/sample counts、色彩/stream metadata、完整解码检查。
- QAReport：overall、checks[{id,required,status,threshold,measured,evidence_refs,output_ranges,reason}]、checker版本；所有required项PASS才允许总体PASS。SKIPPED不能被当成NOT_APPLICABLE掩盖。
- Release：render artifact、QA artifact、selection记录、分支语言、创建者/策略、创建时间。只新增，不覆盖。未实现publish前，demo预览不叫正式release。

## 3. 契约测试和发布纪律

同一节点所有插件共享黄金样例与不合法样例。若某实现只会返回文本不返回位置，应明确该策略能力不满足硬字幕擦除，而不能伪造全屏bbox。能力声明包括文本、时序、位置、字符级、speaker、原生平台等。

契约新增字段的默认值不应改变旧字段语义。对于已有不可变数据，新版本读取器要么显式兼容，要么通过迁移节点产出新版本。禁止打开历史result.json原地添加字段后仍保留旧hash与producer。
