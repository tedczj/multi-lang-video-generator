# 第二期首批实现：N11 / N12

> 后续已接入候选成片链路及 `N12/audio_qa_asr`，最新结果见 [整体验证记录](PHASE_2_FULL_VERIFICATION_ZH.md)。下文保留首批交付的执行身份与当时限制，不代表当前代码仍只有 N11/N12。

日期：2026-09-12。用户确认开始开发后，先交付已选定的 CosyVoice3 zero-shot worker 和音频质检。第二期整体未完成，本文不是成片闭环验收报告。

## 已实现

- `N11/cosyvoice3_zero_shot` 接入主控策略注册表；在私有配置指定的独立 Python 环境中执行。输入是固定的 TranslationSet、VoiceReference 和参考 Audio artifact，按 unit_id 选取译文，校验参考音哈希、区间和模型 revision 声明。
- 完整拼接实际返回块，保留原始 FLOAT WAV、RawDubClip、模型回执和模型源码 ZIP。模型回执记录实际设备、dtype、依赖版本、块采样数和调用耗时；全量权重哈希未测量，明确为 null+原因，不冒充权重验签。
- `N12/audio_qa` 校验原始音频与 RawDubClip 是否一致；完整转为 48 kHz/双声道/PCM16，核对采样数。静音、削波标 FAIL；长前导或无法定位持续能量标 REVIEW。内容/尾音与人工听感检查尚未自动完成，因此没有自动 PASS 路径。
- 主控保存模型声明，并在 retry 时使用原请求的声明；不会因本地配置变化静默换模型。质量 FAIL 被写入 execution，且不能作为后续输入。

前导检测 `energy-v1`：20 ms RMS、连续三窗高于 −40 dBFS。默认持续能量出现晚于 1 秒触发 REVIEW。这是基于已发现问题的临时筛查参数，不是经过真实视频集校准的合格阈值，更不是首字定位。所有音频保持完整，没有自动裁剪、降噪、拉伸或生成结果复用。

## 本轮验证

| 检查 | 本轮结果 |
|---|---|
| 单元回归 | `tests/unit`：16 passed；涵盖前导异常、无损保留原文件、采样转换、静音/削波 FAIL、输入绑定、按 ID 取译文、冻结 retry 模型声明 |
| 现有主控真实 MySQL 回归 | `test_db_store_retry_lineage`：1 passed；沿用隔离验收配置 `local-acceptance-007.json` |
| N11 真实协议执行 | 两次独立 worker/模型调用，均完成真实 zero-shot 合成；相同参考、目标句、种子 42，保留两套 request/result/日志/源码快照 |
| N12 新生成音频 | 两次均检测到约 2.60 秒前导，overall=REVIEW，trimmed_samples=0 |
| 用户指出的旧样本回归 | 原始 `cosy_libri_003/r0_s6.wav` 被检测为前导 REVIEW，未删除任何采样 |
| 静态检查 | 新增 Python 文件 Ruff 检查通过；Git diff 空白检查通过 |

真实 worker 协议证据：[summary.json](../../evidence/phase2_voice_workers_001/summary.json)。这次上游文本与参考元数据由验证脚本人工构建，未调用 Codex，也没有将 N11/N12 这几次执行登记为 MySQL 业务任务。其 version=1/2 是协议测试标签，不是数据库版本验收；不得据此宣称 T-TRANSLATE 或完整 T-RETRY 已通过。

两次真实合成分别为：

- `exec_5fc283f116b64489ab9b3900fe4f29f8`
- `exec_44a5c827bec94dafa08585ddfc7b9e92`

证据包含执行时源码快照；执行后增加的空 unit_id 拒绝分支和格式化由最终单元测试覆盖，未将历史运行标成重新执行。

## 重跑与部署

参考 [worker 配置说明](../../workers/README.md)。本机单独的模型部署对象存于被 Git 忽略的 `config/local-cosyvoice-deployment.json`，包含本轮挂载盘中的模型、源码和独立环境路径。主控使用时需将该对象放进完整本地配置的 `models["N11/cosyvoice3_zero_shot"]` 数组。

在项目虚拟环境运行协议冒烟，输出目录必须是新的：

```bash
.venv/bin/python scripts/verify_voice_workers.py \
  --deployment config/local-cosyvoice-deployment.json \
  --reference evidence/phase2_compare_20260912_001/inputs/librispeech_1272.flac \
  --transcript 'MISTER QUILTER IS THE APOSTLE OF THE MIDDLE CLASSES AND WE ARE GLAD TO WELCOME HIS GOSPEL' \
  --text '谢谢你的帮助，我们明天再见。' \
  --regression-audio evidence/phase2_compare_20260912_001/cosy_libri_003/r0_s6.wav \
  --out evidence/phase2_voice_workers_NEW
```

原来的 [比较报告](phase2_compare_20260912_001.md) 保持为测试与选型历史。音色选择方向为 zero-shot，但前导问题目前是“可被复核拦截”，不是“已经修复”。

## 后续工作

N05–N10 的真实字幕/语音分析、归句、Codex 合批翻译与参考音提取尚未接入；当前 TranslationSet 是 N11 消费侧契约，不代表 N09 的完整批次、响应及语义验证已实现。N16/N18 现有第一期策略仍使用提示音，真实译音时间线、双语字幕、成片和人工复核仍需第二期后续实现。

普通 `doctor` 检查的是现有主控/数据库/媒体基础环境，不代表第二期所有模型就绪。未运行第二期全链路两次重跑、真实烧字 20 帧和人工成片验收，也未提交或发布第三期功能。


## 后续整体验证结果（2026-09-12）

当前候选链路已两次完成真实 OCR、Whisper+Silero VAD、Codex、CosyVoice3、中文 ASR 质检、双语布局和成片，并通过真实数据库翻译/TTS retry、原声与译音 PCM 保真检查。回归共 52 项通过。正式视频真值、自动多人识别、前导异常及人工听感仍有缺项，第二期整体保持 INCOMPLETE / REVIEW；详见 [完整结果、预览与问题表](PHASE_2_FULL_VERIFICATION_ZH.md)。本节补充后续事实，不改变上文首批运行的执行身份或当时验证范围。
