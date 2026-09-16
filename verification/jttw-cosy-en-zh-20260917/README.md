# CosyVoice3 + 英文先、中文后（2026-09-17 修订）

用户否定 Tingting 音色并要求恢复英文→中文顺序。本轮使用原片旁白音色参考实际运行 CosyVoice3，保留原始英文 PCM，重做三句连续慢放 A/B 样片。此前 Tingting 中文先行 v1/v2 是历史版本，未覆盖。

## 模型恢复

在 `/Volumes/备份/#recycle/mlvideo-phase2-model-tests-20260912` 找回权重、源码和环境，原地恢复到旧路径，避免重下模型和破坏绝对路径配置。回收站没有保留 Python 解释器链接，按原 `pyvenv.cfg` 补回 python/python3/python3.11 链接到原 Python 3.11.3；torch/torchaudio 2.3.1 导入成功，随后实际加载并推理验证。
同时保留回收站内的 `mlvideo-speaker-models-20260912` 说话人识别运行目录，补回解释器链接。两目录均增加 `运行模型_请勿清理_README.txt`，不再把这些内容描述成可清理的临时测试输出。

CosyVoice3：`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`，revision `29e01c4e8d000f4bcd70751be16fa94bf3d85a18`；官方 PyTorch CPU FP32、seed 42。每句由现有 `workers/cosyvoice_worker.py` 实际执行，输出 raw FLOAT WAV、ModelReceipt、源码快照和输入绑定。完整权重 SHA-512 未逐文件重算，不将成功加载冒充完整恢复校验。

参考：源片旁白 24.64–35.10 秒，共 10.46 秒，24 kHz mono；与原英文识别文本绑定。没有借用其他角色声音。原片混音含背景音，参考是否适合最终音色仍为 REVIEW。

## 时间线变更

新产物 `ContinuousExperiment.v2` 明确写入 `audio_order=en-zh`、`english_start_sample`、`chinese_start_sample`；每组为完整原声 → 1 秒静音 → CosyVoice3 中文 → 1 秒静音。新音轨生成器拒绝旧 v1，避免静默改变旧产物语义；验证器仍识别历史 v1 的中文先行顺序。
画面继续按实际配音时长线性慢放，每个源帧保序呈现一次。中文仍随源字幕页出现，声音顺序改变不会把字幕限定为配音时才显示。保持 1920×1080，不重绘英文、不加底栏、不插帧、不剪短或加速语音。

## 复现

从仓库根目录运行。请求和媒体在 `evidence/jttw-cosy-en-zh-20260917/`；原片与无损短样在上一轮 evidence 目录。重新合成必须新建版本输出，不能覆盖本轮音频。

```sh
# 单句 worker 的真实调用形式（request 内 output_dir 也必须指向新版本）
'/Volumes/备份/mlvideo-phase2-model-tests-20260912/cosy-env/bin/python' \
  workers/cosyvoice_worker.py \
  --request <新版本的request.json> --result <新版本的result.json>

.venv/bin/python -m mlvideo.continuous \
  --manifest evidence/jttw-cosy-en-zh-20260917/experiment.json \
  --output '/Volumes/备份/视频成片_请勿清理/jttw-cosy-en-zh-20260917/ab-rerun'

.venv/bin/python -m mlvideo.continuous_verify \
  '/Volumes/备份/视频成片_请勿清理/jttw-cosy-en-zh-20260917/ab-rerun' \
  --report evidence/jttw-cosy-en-zh-20260917/ab-rerun-checks.json

.venv/bin/python -m pytest tests/unit tests/integration/test_continuous_experiment.py -q
```

仓库提交小型报告与复现信息，完整视频、逐句原始音频及恢复环境保留本地/NAS。测试日志包含 107 passed；语音内容用 Whisper small 辅助复核，ASR、能量和解码均不能代替真人音色/自然度验收。

## 本轮实测结果

- CosyVoice3 原始中文时长：5.64 / 5.84 / 3.12 秒。三句 raw FLOAT 音频分别通过解码、非静音、削波、前导能量检查；没有自动剪头尾、变速或替换音色。
- Whisper small 辅助转录见 `asr-checks.json`，三句与目标语义/文字一致（结果为繁体，标点略异）；不据此标记真人试听通过。
- 输出：36.616 秒、1920×1080，连续版 480 帧、停帧版 1100 帧。`media-checks.json` 全量解码并核对英文先行、两版 PCM、源帧映射及 MP4；`legacy-v1-checks.json` 证明旧中文先行产物仍可审计。
- 视觉抽查：已检查新成片 contact sheet 的三页及 6.3 秒英中间隔画面，英文仍保留、中文紧邻其下方、静音时不消失。没有对全部输出帧逐帧做人工审查。
- 人工试听与正式验收：REVIEW。原片背景音乐衔接、动作慢放与旁白同步关系的限制继续保留。

本机磁盘保护阻止了本地渲染；实际输出在：
`/Volumes/备份/视频成片_请勿清理/jttw-cosy-en-zh-20260917/ab-v1/`。
仓库 `evidence/jttw-cosy-en-zh-20260917/ab-v1` 链接到它。需要 NAS 挂载才能播放/重新审计这套成片；逐句声音和输入仍在本机 evidence。
