# Phase 2 第一轮执行与音色试听

2026-09-13。用户要求分两轮，第一轮控制在 13 GB 内。本轮已完成前 117 秒的真实预处理、整片参考音重建和两组新音色的 12 条中文试听。**尚未生成前 117 秒的双语候选成片；Phase 2 仍为 INCOMPLETE，Phase 3 未开始。**

当前可直接复核：[A/B 试听页](http://127.0.0.1:63881/)；服务停止后可打开[本地页面](/Volumes/mlvideo-p2-round1/evidence/voice-audition/index.html)。先比较每组英语参考和中文声音，重点听 A 第 1/4 句及 B 第 5 句。ASR 显示语词/专名差异，但不能单凭识别文本断言发音错误。

## 执行结果

| 项目 | 本轮证据与边界 |
|---|---|
| 有限容量工作区 | 本机 APFS 稀疏工作卷，映像上限 12,288,000,000 字节，其余 712 MB 预算留给卷外日志与数据库记录。检查时卷内使用约 4.87 GB，稀疏包实际分配约 **6.01 GB**，均低于 13 GB；见 [summary.json](/Volumes/mlvideo-p2-round1/evidence/summary.json) |
| Docker 恢复 | 实测共享虚拟磁盘 ext4 因 I/O 错误变成只读，MySQL healthy 不能证明可写。用户明确批准后重启 Docker，恢复 MySQL、MinIO、storage-client；独立数据库建表/写入成功，指定旧分析、参考库及复核 execution ID 仍在。未重置 Docker 数据；见 [恢复记录](/Volumes/mlvideo-p2-round1/evidence/docker-recovery/write-recovered.json) |
| 前 117 秒预处理 | 新建数据库 `mlvideo_phase2_round1_20260913_001`，数据根 `/Volumes/mlvideo-p2-round1/data`。真实规范化、351 帧 PaddleOCR、Whisper/Silero VAD、说话人分析及参考库执行均登记 MySQL；[首次执行清单](/Volumes/mlvideo-p2-round1/evidence/preparation.json) |
| Opus 截取修复 | 新回归复现 `-ss 0` 导致少保留 24 个采样帧；零起点不再执行不必要的 seek。真实 N04 retry `exec_a061b36a4803453e82bc1a78fd45bbff` 保留全部 5,615,688 个源 PCM 帧，3507 个视频帧的像素及时间戳与旧版本完全一致；[独立验证](/Volumes/mlvideo-p2-round1/evidence/canonical-repair-verification.json) |
| OCR 抽查 | 20 张原片画面单独调用真实 OCR。按助手视觉转录比较，17/20 帧字母数字文本一致；不是盲测人工真值，也未计算框 IoU。96.096 秒字幕在 1280 检测尺度漏读，1920 尺度补测读出，原失败结果保留。独立脚本曾错误套用 720 高度 ROI，已另存按实际 1080 高度重算的报告，原模型响应不变；[比较记录](/Volumes/mlvideo-p2-round1/evidence/ocr-visual-comparison.json) |
| 分组与参考音 | 原声学模型对整片/短窗的标签匹配后仅约 65% 一致，此值不是 DER 或人物识别准确率。另用公开 WeSpeaker ResNet34-LM 对照，并从完整原片重新生成 FLOAT 原音及在 PCM16 转换前统一降幅的参考副本。整片得到 **10 个估计组，6 组有技术合格参考**；第一轮涉及的组 06/07 仍缺参考，全部分组仍待确认；[新参考库缺项](/Volumes/mlvideo-p2-round1/evidence/full-reference-readiness.json) |
| 中文音色试听 | A 采用旁白文本参考，B 采用首轮人物对白文本参考；两者均通过 N10 登记并绑定 bank、来源区间及 WAV 哈希。官方 CosyVoice3 CPU FP32，一次加载后逐句真实生成，每组六句固定校准文本；每句完整 WAV、全部返回块采样数、模型/参考身份和 Whisper 中文 ASR 均保留。12 条均未触发静音或削波 FAIL，所有听感结果仍为 REVIEW；[试听清单](/Volumes/mlvideo-p2-round1/evidence/voice-audition/manifest.json) |
| 自测 | **88 passed，11 deselected**。包含新 Opus 回归、已有字幕/音频/媒体检查及真实 MySQL 血缘/配方回归；未重跑故障注入、磁盘耗尽、备份重启和下载用例。修改代码 Ruff、`git diff --check` 通过；[JUnit](/Volumes/mlvideo-p2-round1/evidence/regression-final.xml) |
| 试听交付检查 | 14 个音频入口（2 个英语参考、12 个中文）HTTP HEAD 均 200；原始/播放 WAV 哈希和完整采样数检查通过。Chrome 连接失败，本轮未取得浏览器像素验收；[HTTP 检查](/Volumes/mlvideo-p2-round1/evidence/voice-audition/http-verification.json) |

音色校准使用明确记录的固定中文测试句，**不是新的 Codex 翻译结果，也不是 N11 业务成片执行**。参考提取已登记 MySQL；驻留模型试听和中文 ASR 为独立模型检查，记录在本轮不可覆盖的新证据目录。不得将其数量当作整片翻译或说话人质量验收通过。

旧 OCR 保持原输入绑定。修复后选择它与新 PCM 一起使用的依据是完整视频帧像素/时间戳一致性证明；未修改旧 caption artifact。第一版 N04、失败的数据库初始化日志和所有原始模型响应均保留。

为控制本轮容量，仅清理了本轮成功执行中与已封存 artifact **逐字节相同**的大型工作副本，先校验哈希、记录恢复来源，再清理并复验封存文件；没有删除旧证据、封存结果、请求或日志。两次共约 2.39 GB 的工作卷空间被回收。稀疏包并不立即按同等数额缩小，容量报告使用实际分配量，不能把卷内空闲当作主机已释放空间。

## 下一步

1. 先试听 A/B，确认声音像不像英语参考、是否自然，以及疑问句是否吞字/错读。选择音色与确认整片质量是分开的决定。
2. 核对第一轮剩余分组和缺少参考的短声段，完成绑定准确 PCM 的分组/参考音复核，再推进真实逐段翻译和双语候选。
3. 完成第一轮成片与全项验证后，依据实际占用安排第二轮。正式单/多人素材矩阵、全片与人工质量验收仍未完成。

工作卷位于仓库 `evidence/phase2_round1_001/workspace.sparsebundle`。重启电脑后如未挂载，可在仓库运行：

```bash
hdiutil attach -nobrowse -mountpoint /Volumes/mlvideo-p2-round1 \
  evidence/phase2_round1_001/workspace.sparsebundle
```

本次 WeSpeaker 实验来自 [Sherpa 官方预训练模型入口](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html)；[对应模型说明](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM) 标注 CC-BY-4.0。下载来源、大小、SHA-512 和实际部署声明均保留；它是替换 embedding 的对照，不能冒充完整 Community-1 管线或已通过质量验收的生产选型。
