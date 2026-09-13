# Phase 2 收尾检查与待完成项

> 2026-09-13 后续已按 13 GB 预算开始第一轮真实执行，修复 Opus 零起点截取，完成参考音对照与 12 条中文试听；详见 [第一轮结果](PHASE_2_ROUND1_20260913_ZH.md)。下文保留前一轮 87 项回归和当时的分组/空间状态。

2026-09-12。本轮完成说话人/参考音复核入口、逐条音色验收检查和相关自测。**Phase 2 整体验收仍为 INCOMPLETE；真实整片候选尚未生成，Phase 3 尚未开始。** 代码测试通过不等于分组可靠性、听感或整片质量通过。

## 已完成的修改

- `N08/reviewed_speech` 消费固定原音频、ASR、说话人轨和 VAD。人工决定必须绑定四个输入的 SHA-512，具名确认分组、语音边界与转录。原始分析保留，新执行输出修订后的 SpeechTrack、SpeakerTrack 和决定；不允许遗漏 ASR、VAD 或仅由聚类检出的人声。
- 参考音库拒绝越界和无效区间；前后补留区间如带入转录未包含的相邻人声，拒绝该候选。时长、对白混入风险和补留失败均保存具体 speech ID 与原因。模型消费侧拒绝重复、交叠或倒序的拼接参考区间。
- `N10/from_speaker_bank` 支持绑定 bank、candidate、speaker 和原音频哈希的参考音复核。`candidate` 的 `speaker_bank` 路径要求已登记的 `N08/reviewed_speech` 来源和逐说话人参考音批准，核对与当前规范化 PCM 的哈希一致，并使用 `cosyvoice3_from_bank`。参考音验证在翻译与合成前执行。
- N19 为每条译音增加音色、自然度、前导杂音、尾音、口播内容五项人工检查；少复核任何一条都不能通过。检查仍绑定同一 render 和全部实际 raw clip，不自动生成听感批准。
- 新增 `scripts/prepare_speaker_review.py`，输出全部分组、出现时间、试听片段、参考候选、来源哈希和待填写决定。模板 reviewer 为 null，分项为 REVIEW。

这些修改支持人工校正和验收，不代表自动分组模型已经达到可靠人物识别水平，也没有完成独立 forced alignment 部署或全项正式验收矩阵。原 `verify_phase.py --phase 2` 仍是 INCOMPLETE 入口。

## 本轮实际执行

| 范围 | 结果 |
|---|---|
| 单元、媒体、真实 MySQL 回归 | **87 passed，11 deselected**；本轮没有执行重启/故障注入、磁盘耗尽或下载测试。命令及 [JUnit](../../evidence/phase2_closeout_001/regression-final.xml) 见下方 |
| 新增逻辑检查 | 覆盖旧输入哈希、空/重复/缺少复核项、遗漏聚类独有人声、音频区间重复、补留带入额外人声，以及逐条尾音缺项；worker 输出经真实 schema 校验 |
| 整片参考音库重建 | 新 execution `exec_7a6348d17fbb4ced80270cd62a6658ce`，使用此前完整原音频和分析，实际重新提取参考音；没有重跑模型，也没有改写旧库 |
| 参考库结果 | 43 个估计分组中，6 组共有 9 个技术候选；**37 组没有可用参考**。9 个候选仍待纯净度、转录和人物归属试听 |
| 试听包完整性 | 91 个 WAV（82 个分组片段、9 个参考候选），哈希和完整 PCM 解码均通过；不是听感通过 |
| 真实 N19 回归 | 对已存在的真实三段成片新执行 `exec_ed157a2d435f42308fcceea45dec8bda`，输出 26 个检查，包含 15 项逐条声音复核，结果 **REVIEW**；本轮未重新合成或渲染该短片 |
| 静态 | 修改的业务/worker/新增脚本和测试 Ruff 通过；`contracts.py` 的既有导入排序问题未做无关调整。`git diff --check` 通过 |

[试听与分组复核入口](../../evidence/phase2_closeout_001/speaker-review/index.html) · [全部缺项](../../evidence/phase2_closeout_001/speaker-review/readiness.json) · [试听文件验证](../../evidence/phase2_closeout_001/speaker-review/verification.json) · [三段成片的新声音复核模板](../../evidence/phase2_closeout_001/voice-decision.pending.json)

回归命令：

```bash
MLVIDEO_TEST_CONFIG=config/local-acceptance-007.json \
MLVIDEO_EVIDENCE=evidence/phase2_closeout_001/regression-final \
.venv/bin/python -m pytest tests/unit tests/integration/test_phase2_media.py \
  tests/integration/test_phase1.py \
  -k 'not recover_windows and not lock_timeout_kill and not backup_restore_restart and not bounded_disk_full and not database_disconnect and not downloader_real_http' \
  -q --junitxml=evidence/phase2_closeout_001/regression-final.xml
```

## 阻止整片验收的实际条件

1. **说话人分组和参考音未确认。** 43 是模型聚类数，不是已经确定的角色人数。不能通过更改阈值或给缺少参考的分组借用其他人物声音来宣称完成。用户尚未提供修订后的分组/转录或新声音的实际试听决定。当前试听包使用已有整片参考 PCM；未来候选若使用不同 PCM，必须重新核对绑定，不能直接沿用旧哈希批准。
2. **产物盘空间不足以支撑当前规模估算。** 完整原片实测约 902.653 秒；目标挂载盘当时剩 4,426,518,528 字节（约 4.12 GiB）。按已有 13.614 秒预览的规范化视频和母版大小线性外推，一轮整片这两项约 25,416,713,434 字节（23.67 GiB），不含工作副本、OCR 和第二轮产物。这是估算，不是容量上限或已生成文件大小。[实测与估算记录](../../evidence/phase2_closeout_001/full-film-preflight.json)
3. **正式质量矩阵仍缺少材料。** 单/多说话人真实素材、独立 OCR 20 帧真值、语音边界/对齐、翻译与新译音听感、两轮整片和真实 retry 仍需按 kickoff 完成；本轮短片复核入口和单元测试不能替代它们。

已向用户请求可用产物空间和具体分组复核。旧文件、原声和失败/待复核结果均保留；未发布、未提交 Git。后续需完成这些条件、真实整片与完整自测后，才能宣布 Phase 2 完成并进入 Phase 3。
