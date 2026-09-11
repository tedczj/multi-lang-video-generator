# 本次实际验证结果

> 历史参考，2026-09-11 补充说明：本文原文来自 [video_interleave_v2_design_and_skeleton.zip](../sources/video_interleave_v2_design_and_skeleton.zip)，内部路径相对于包内 `video_interleave_v2/`。实现/测试状态均指该参考包，未在本次更新中重跑；当前 MySQL 串行方案见 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和 [文档入口](README_ZH.md)。下方保留原始内容。

## 已执行

- 环境：Python 3.13.5，Linux x86_64。
- FFmpeg：`ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers`。
- 源码commit：`374e5d35ad31e039bed2e1aa4dee25f1cc0c9e4c`；测试与演示运行时 `dirty=false`。
- pytest：**48 passed，2 skipped，0 failed/error**，共50项收集用例。
- 48项通过包含44项逻辑/契约/进程/存储用例及4项真实FFmpeg媒体集成；随机时间线的500个子案例包含在一个测试内，不额外计500项。
- 同输入/参数重试实际媒体节点：原执行`exec_f069ea6480904193b5c60d283bca2e97`，新执行`exec_4c849c323adf4fa7ab1cb28cfb198f2f`，版本v2，产生9份新子进程stdout记录。

## 实际媒体样例

| 项目 | 实测 |
|---|---|
| 原视频 | 12秒，25fps，300帧 |
| 译音长度 | 1秒、3秒、2秒 |
| 额外定格 | 0帧、75帧、50帧 |
| 输出 | 17秒，425帧 |
| PCM | 48kHz，双声道，816000个采样帧 |
| 原画面顺序和定格来源 | 无损逐帧hash完全符合独立期望 |
| 原音/译音位置和完整性 | PCM逐采样完全符合独立期望 |
| H264/AAC预览 | 完整解码通过 |
| 综合QA | REVIEW；5类AI/字幕检查未执行 |

示例语音使用不同频率提示音，不是真实英语/中文。精确一致性针对FFV1/PCM，不针对有损H264/AAC。没有在用户Mac或NVIDIA环境实测，不提供未测速度和准确率。

## 明确未执行

两个SKIP分别为真实YouTube联网重复下载、两种真实OCR策略的AB与重试。当前容器没有安装yt-dlp，未进行授权视频下载；真实OCR/VAD/ASR/字幕修复/字体匹配/翻译/克隆模型未接入。它们的可执行入口和生产验收计划不构成已通过证明。

## 查看证据

- `test-results.xml`：pytest JUnit结果。
- `environment.json`：实际工具/源码环境检查。
- `demo/verification.json`：媒体精确检查和源码指纹。
- `demo/timeline.json`：实际空档优先计划。
- `demo/qa.json`：未执行质量项的SKIPPED原因。
- `actual-render-retry.json`：真实重试的新执行版本。
- `demo/workspace/videos/<sha512>/events.jsonl`：该源视频的统一日志。
- 对应`nodes/*/*/v*/request.json`、`manifest.json`与`source.snapshot.zip`：输入输出/代码/参数/产物证据。

源码包内没有字体、第三方模型权重或复制的上游项目源码。执行命令日志中的绝对路径是本次环境真实路径；在其他机器再次运行，内核会按新资产位置解析artifact并构建新命令，不应原样粘贴旧日志命令。
