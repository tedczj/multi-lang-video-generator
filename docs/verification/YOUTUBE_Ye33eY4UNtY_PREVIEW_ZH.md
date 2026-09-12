# 用户指定原片的硬字幕预览与验证

> 最新已交付 [三段连续原文预览](YOUTUBE_Ye33eY4UNtY_THREE_SEGMENTS_ZH.md)，并修复中文提前消失的问题；下面保留单句历史。

> 最新排版已按用户反馈调整为 **1280×720、画面内字幕**：中文优先在英文下方，空间不足则在英文上方，不加黑边。[播放更新后的预览](../../evidence/youtube_Ye33eY4UNtY_inline_001/preview.mp4)。下文的 1280×864 是保留的首版历史。音色问题及原版 Mac 配置实测见 [后续报告](COSYVOICE_MAC_AND_SPEAKER_OPTIONS_ZH.md)。

日期：2026-09-12。原片：[Rocket Girl Fights a Hypnotic Villain — Little Fox](https://www.youtube.com/watch?v=Ye33eY4UNtY)，903 秒。本轮使用用户提供的实际视频，不使用此前纯色背景控制样本。

## 字幕来源与预览范围

原片**确实包含烧录在画面中的英文字幕**。YouTube 的可下载人工字幕轨列表为空，只说明没有对应的独立轨道，不说明画面没有字幕，也不说明画面文字是自动生成的。本轮以画面英文为文本依据，YouTube 自动字幕只用于先导定位；原始自动字幕和下载元数据均保留，未作为最终译文的源文本。

用户截图中的原句：`She saw one student pull something sparkly from the wall.`

预览使用原片 562.160–565.880 秒（9:22.160–9:25.880）的完整一句。较大范围的 Whisper/VAD 先导分析把该句估计为 562.40–565.66 秒，选取窗口保留首字及尾音余量。该边界是模型估计加画面核对，不冒充人工强制对齐真值。

完整原片已下载并登记不可变资产；预览是该资产的 N04/excerpt 子产物，保留原 artifact 引用、SHA-512、请求区间和执行命令，没有把派生短片假装成原始 YouTube 下载。

- acquisition：`acq_6700025cd9714cf18693edcf23883b6d`
- source artifact：`art_fe18e00aeeeb40f0a809b2a5db37d8c5`
- asset SHA-512：`ceb2a514cb1318c3d8bc161bcb09f4c21a9dbb0d4fdb7d66bf41bb2c7ab62acb6fd070f2f8869dd09c4e5d9cc8c07f18b824030ab70ac8e7dbd181bde0a04e61`

[下载记录](../../evidence/youtube_Ye33eY4UNtY_001/download-result.json)、[完整源引用](../../evidence/youtube_Ye33eY4UNtY_001/source.json)。较大资产放在挂载盘 `/Volumes/备份/mlvideo-Ye33eY4UNtY-20260912/data`，使用独立 MySQL 数据库 `mlvideo_youtube_20260912_001`；旧验收数据库与文件保留。

## 本轮针对真实素材的修复

1. **片段音频精确截取**：新增 N04/excerpt，快速定位前留 1 秒预读，再在输出端精确截取。独立 PCM 用例发现，只有输入侧 `-ss` 会跳过包含目标起点的 PCM 包；修复后验证所选原始采样区间完整且一致。规范化所需的时钟补齐单独记录，不能把容器时长直接等同于实际讲话长度。
2. **硬字幕文字来源**：N08 增加 `caption_consensus`。按实际帧和文字框阅读顺序合并行，忽略空白后字符跨帧一致才采用画面 OCR 文本，空格形式使用出现次数最多的实际读数，次数相同则拒绝；出现换页或字符分歧就拒绝继续，不用 ASR 静默替换字幕。该模式目前用于单个完整字幕页的短预览，不能当作全片多页归句已经完成。
3. **长字幕 OCR 参数**：初始实际执行出现粘词、漏字，原始输出保留并在 N08 被拦截。对同一失败帧实测 `max1280`、`max960`、`max1280_tight`，后者（`textDetLimitSideLen=1280`、`textDetLimitType=max`、`textDetUnclipRatio=1.1`）能正确识别整句。该差异支持检测框扩张/裁切影响识别的判断，不将其推广为所有字幕的最优参数。请求参数进入固定模型部署声明，旧 retry 不会静默改用新参数。[原始对比](../../evidence/youtube_Ye33eY4UNtY_001/ocr-profiles/)。
4. **保留已有英文**：N17/N18 支持 `preserve_source_english`。原片缩放为 1280×720 预览后，增加 144 像素底部区域，仅在该区域绘制中文；不会再次绘制英文，不擦除原字幕，也不遮挡原画面。原视频区域和定格帧中的英文由源像素保留。

中文仍由真实 Codex 翻译，CosyVoice3 zero-shot 使用这段旁白自己的参考音。speaker ID 为本次单句参考音组名，不代表已完成人物身份识别或全片角色映射。配音保留全部返回块，并经过原有 PCM/前导/削波检查及中文 ASR 辅助检查。

## 执行历史与边界

- `preview-result-001`：原始 OCR 参数在真实长字幕上失败，N08 拒绝使用错误文本；上游请求、图像和响应均保留。
- 第 2 次运行：为改用已通过独立 PCM 检验的精确片段截取而主动停止，不能计作成功或质量失败样本。
- 第 3 次运行：精确截取和收紧检测框后的 28 个帧字符已一致，但 17 帧为 `the wall.`、11 帧为 `thewall.`，原来的逐字符含空格严格共识仍拒绝继续。
- 后续仅修正空格共识规则，真实 retry N08，复用第 3 次运行中已成功且哈希固定的上游 artifact，再用新的 recipe run 完成翻译到成片；旧失败 run 不改写成成功。输出预览和实测验证结果见下方。

此报告只针对截图这句的真实预览与相应技术验证。全片 15 分钟翻译配音、所有角色映射、多页字幕归句以及整片质量验收尚未完成；没有把短片成功外推为全片通过。机器质检和助手画面核对不能替代用户听感决定。

## 已生成的真实预览

- [播放预览 MP4](../../evidence/youtube_Ye33eY4UNtY_001/delivery/preview.mp4)
- [查看实际成片截图](../../evidence/youtube_Ye33eY4UNtY_001/delivery/preview.png)
- [单独试听中文](../../evidence/youtube_Ye33eY4UNtY_001/delivery/chinese.wav)

本次预览总长约 **9.91 秒**，分辨率 **1280×864**。上部 1280×720 是带原英文字幕的源预览画面；下部 144 像素仅显示新增中文。先播放英语原声，再播放完整中文克隆语音。

- 新 recipe run：`run_f6d3b902e98647f1b1b13f63e1ada4f6`；使用固定的成功上游输出，并保留旧失败 run 状态。
- 源预览：112 帧、179,379 个规范化采样帧；其中请求区间的音频有效内容为 178,560 帧，其余为帧/音频时钟补齐。
- 成片：297 帧、475,675 个采样帧，29.97 fps（30000/1001）、48 kHz。
- 中文从 4.7370625 秒开始，完整中文 199,680 帧（4.16 秒）；保护区结束到中文开始为 48,000 帧（1 秒），中文结束后还有约 1.013 秒。
- 实际译文：她看见一名学生从墙上拉出了一个闪闪发亮的东西。

[完整执行及 artifact 对应](../../evidence/youtube_Ye33eY4UNtY_001/preview-bundle.json)、[可核对来源的交付副本 manifest](../../evidence/youtube_Ye33eY4UNtY_001/delivery/manifest.json)。

## 实测验证与剩余限制

| 检查 | 本轮结果 |
|---|---|
| 下载与血缘 | 实际下载指定 YouTube 原片；预览仍在原 asset 内，N04/excerpt 引用固定 source artifact 和哈希 |
| OCR | 同一字幕页 28 帧在收紧检测框后非空白字符全部一致；17 个标准空格形式、11 个粘词空格形式均保留。不是 28 种字幕场景，也不代替完整真实视频 OCR 验收矩阵 |
| 原声和译音完整性 | [离线 PCM 审计](../../evidence/youtube_Ye33eY4UNtY_001/audit/run-1/audit.json)：规范化源片段全部 PCM、完整译音插入、母版解复用 PCM 均一致 |
| 原图与英文字幕 | [逐帧像素审计](../../evidence/youtube_Ye33eY4UNtY_001/pixel-audit.json)：297 帧母版上部区域与源片段/定格映射逐帧哈希一致；中文覆盖层在原画面区域完全透明，没有重绘或覆盖原英文 |
| 中文音频质检 | 可解码、非静音、未触发峰值削波；持续能量起点为 0 秒，trimmed_samples=0。它不能证明没有细小前导声音或音色自然 |
| 中文 ASR | 原始识别为“他看見一名學生從牆上拉出了一個閃閃發亮的東西”，有繁简体及同音“他/她”差异，原始 CER=45.45%；该数值不能直接当作发音错误率。[原始结果](../../evidence/youtube_Ye33eY4UNtY_001/delivery/content_asr.json) |
| 自动化回归 | 42 passed，包含精确截取 PCM、硬字幕空格共识、拒绝字符不一致、扩展画布与原像素保留。[JUnit](../../evidence/youtube_Ye33eY4UNtY_001/regression-final.xml) |
| 视觉检查 | 助手查看实际成片截图，确认原英文仍在原画面中，只有底部中文新增；用户听感/最终画面偏好未确认 |

最终状态仍为 **REVIEW**：这是真实指定原片的一句短预览，不是整片 15 分钟成片或全部角色克隆验收。不会因自动解码、ASR 或像素一致性检查通过而自动生成用户的听感批准。
