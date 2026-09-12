# 第三期 kickoff：字幕清理与完整交付

状态：待开发；第二期候选链路已交付，正式验收尚未通过，当前先完成第二期收尾。基线：[完整开发计划](../DEVELOPMENT_PLAN_ZH.md)，完成 REQ-02、REQ-05–06 的完整交付，并对 REQ-01、REQ-03–04 做全链路回归。命令和脚本为待交付接口。

## 1. 前置条件与范围

[第二期](PHASE_2_KICKOFF_ZH.md) 已实现候选链路，并交付用户指定 YouTube 原片的三段连续预览。用户“ok 先这样”确认当前预览方向作为暂定基线，未确认音色相似度或第二期完整验收。证据见 [三段预览报告](../verification/YOUTUBE_Ye33eY4UNtY_THREE_SEGMENTS_ZH.md) 与 [模型/参考库及 Mac 实测](../verification/COSYVOICE_MAC_AND_SPEAKER_OPTIONS_ZH.md)。

正式进入本期验收前，还需收尾：

1. 使全片说话人分组和参考音选择可靠，并完成音色、内容、尾音的试听复核；当前声学分组数量不稳定，不能作为已确认人数。
2. 用真实整片完成候选生成、固定输入重试及自动/人工质量检查。现有约 30 秒预览不等同于 15 分钟整片已完成，第二期 `--phase 2` 目前仍输出 INCOMPLETE。
3. 固定交接所用的源码、模型、配方、输入及验收证据。CosyVoice3 CPU FP32/8 线程和参考缓存已做基准；常驻模型仅在基准进程验证，业务常驻 worker 仍是待落地的性能优化，不能描述为已经上线。

当前已确认的预览规则：保留原英文，中文放画面内，默认英文下方、空间不足时上方；不加黑边，中英文同步保持至原页面切换或片尾。对此类素材优先延续保留英文的分支；N13–N15 清理/样式重建能力另按有清理需求的素材验收，不因已有硬字幕就自动擦除。

保留已有代码/模型身份、固定样本与全部证据；继续使用单个 Mac 主控、单实例 MySQL、顺序 worker。

新增真实硬字幕素材、人工字框/风格标注、干净背景烧字对照样本、字幕之外必须保留的标题/路牌、镜头切换和至少 30 分钟长片。准备第二种真实 OCR 策略、字体候选与清理 worker，先核查实际环境、模型与字体来源。

本期交付完整硬字幕双语成片、QA 与人工复核、可独立校验的本地发布清单，以及完整备份恢复证据。

## 2. 开发与验收顺序

| 顺序 | 节点 / 任务 | 接口和交付 | 验证 ID |
|---|---|---|---|
| 1 | N06 第二 OCR 策略顺序对照 | 同源固定输入，A/B 各有版本；下游选择 A 不因 B 完成而改变 | T-OCR、T-LINEAGE |
| 2 | N13 字体与样式 | CaptionTrack+截图 → StyleProfile；多截图候选重渲染、中文 fallback | T-STYLE |
| 3 | N14 掩膜 | ROI+CaptionTrack+场景 → MaskTrack；时序和 canonical XYXY 明确 | T-CLEAN |
| 4 | N15 字幕清理 | CanonicalMedia+MaskTrack → CleanVideo；只消费清理画面 | T-CLEAN、T-MEDIA |
| 5 | N17–N18 样式布局与完整合成 | 固定风格、清理画面、时间线和英中语音 → 成片 | T-LAYOUT、T-RENDER |
| 6 | N19 完整 QA 和人工复核 | 逐检查证据、固定 render/timeline 引用、ReviewDecision | T-QA |
| 7 | 长片和全部故障窗口 | 有界分段/流式媒体处理，超时/中断/写满/篡改恢复 | T-LONG、T-FAULT、T-RECOVER |
| 8 | 全量备份与隔离还原 | 完整数据库、媒体、模型回执、QA/发布引用 | T-BACKUP |
| 9 | N20 本地发布 | Render+PASS QA+有效人工复核 → Release | T-RELEASE |

参考 [主计划节点表](../DEVELOPMENT_PLAN_ZH.md#6-业务节点与顺序) 和 [实现参考](../IMPLEMENTATION_REFERENCES_ZH.md) 的 REF-OCR、REF-CLEAN、REF-FONT、REF-MEDIA、REF-QA、REF-CORE。VSR 框坐标顺序必须显式转成项目 XYXY；修复模型选择写入部署锁，不继承上游隐式全屏区域或音轨回填。

## 3. 关键输入与质量规则

清理 worker 只接收明确的 MaskTrack，不默认删除画面中所有文字。逐镜头保留掩膜、输入帧与输出帧；场景内重试创建新版本，重新汇总 CleanVideo 时固定各 scene artifact，旧成片不自动更新。

StyleProfile 记录字体文件、字号/颜色/描边/阴影、候选分数与重渲染误差。候选未知、近似替代均如实标注；中文 fallback 需要字形覆盖和实际像素检查。新绘双语默认英上中下；保留原英文的场景遵循用户确认的位置规则：中文优先下方、放不下时上方。两种情况均同步显示，不改变第二期的 batch/TTS/1 秒停顿参数。

清理后的画面必须保持 canonical 帧数和 PTS；对掩膜外像素变化用无损中间结果检查，不能用有损预览差异代替修复越界证据。遮挡背景是估计结果，有干净底图才可计算恢复误差。

完整 QA 遵循 [主计划 §9](../DEVELOPMENT_PLAN_ZH.md#9-qa复核与发布)：必选 FAIL 阻断，REVIEW/SKIPPED 阻止 PASS；不适用必须有素材证据。人工决定绑定当前 render/QA/字体或模型结果，任何被审内容变化后重新复核。

## 4. 真实验收与命令

每个素材在 `tests/fixtures/manifest.json` 固定 SHA-512、人工真值与授权说明。至少运行：

- 可验证的烧字样本：已知背景、字体、字幕区域和时间，字幕外带标题/路牌；统计残字和掩膜外误改。
- 同一素材两种真实 OCR：比较文字、边界和位置误差，明确各模型置信度含义；不只比较输出行数。
- 一条完整硬字幕英语素材：从获取、翻译、克隆到清理、字幕、发布；逐句检查英中配对和音色。
- 至少 30 分钟长片：固定首/中/尾检查区间、讲话边界与时钟预期；记录主控、worker、MySQL 和 Docker 资源峰值。
- 故障样本：worker 被杀/超时、输出半写入、磁盘配额耗尽、索引提交中断、输入篡改、缺少质量证据。

实现后在项目根运行：

```bash
python -m mlvideo.cli doctor
python -m pytest tests/unit -q
python scripts/verify_phase.py --phase 3 \
  --fixtures tests/fixtures/manifest.json --out evidence/phase3_run_001
```

阶段入口先复核第一/二期交接身份与本次差异；变化涉及的契约/媒体/模型测试必须回归。完整业务必需模型、质量和人工检查不得 SKIP。人工结果文件由真实复核产生，不由测试脚本自动填 PASS。

发布命令是本期新增的目标接口；所有 ID 使用实际返回值：

```bash
ASSET_SHA512='<本次源资产的完整 SHA-512>'
RUN_ID='<本次固定 run ID>'
RENDER_ARTIFACT_ID='<本次成片 artifact ID>'
QA_ARTIFACT_ID='<本次 PASS QA artifact ID>'
REVIEW_ARTIFACT_ID='<本次有效人工复核 artifact ID>'
python -m mlvideo.cli release "$ASSET_SHA512" "$RUN_ID" \
  --render "$RENDER_ARTIFACT_ID" --qa "$QA_ARTIFACT_ID" \
  --review "$REVIEW_ARTIFACT_ID"
```

发布只生成本地不可变清单和 releases 记录，遵循与节点产物相同的封存/登记顺序。拒绝旧复核与新成片混配，拒绝缺检查或已被篡改的输出；中断时按同一发布身份核对封存清单，不能生成伪成功记录。

## 5. 全量恢复与完成标准

暂停任务并取得同一运行锁备份；还原到隔离库与新数据根，核对数据库表、所有媒体摘要、每条输入/父级、模型回执、QA、人工决定和发布清单。备份不可通过“数据库导出成功”单独验收。

交付完整硬字幕成片、无损母版、字幕文件、逐句英中/音频对照、关键帧对照、实际模型与代码锁、长片资源报告、全量恢复报告，以及 T-STYLE、T-CLEAN、T-QA、T-LONG、T-FAULT、T-BACKUP、T-RELEASE 的证据。既有 T-OCR/T-LINEAGE/T-MEDIA/T-LAYOUT/T-RENDER/T-RECOVER 的相关回归通过。

本期完成意味着 [主计划 §12](../DEVELOPMENT_PLAN_ZH.md#12-分期交付与文档完成标准) 三期标准全部达成；上游仓库存在某个功能、历史包通过若干单测或一条短演示可播放，都不能替代上述验收。
