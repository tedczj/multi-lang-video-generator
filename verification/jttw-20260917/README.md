# 西游记连续画面：取证、执行与验收记录

## 结论

与 [用户给出的原片](https://www.youtube.com/watch?v=73XAgADpBqY) 相比，录屏正文的主画面映射为 `source_time ≈ 0.534667 × recording_time + 9.816766`。主要增加时间的方法是连续慢放画面，而非每句中文额外冻结画面。双语 ASR 显示中文先于英文，但有幻觉/边界误差；参考作者用什么软件、是否变速或重配英文，仍未知。

`motion/report.json` 保存取样方法、输入 SHA-512、拟合覆盖和误差；`motion/matches.json` 保留所有点（包括歧义/遮挡点）。首次未指定 Node 的下载失败，使用项目既有 `--js-runtimes node` 后取得最高分辨率 1920×1080、399+251；真实时钟为 30000/1001 fps。

## 当前交付

媒体位于仓库根目录的 `evidence/jttw-analysis-20260917/ab-v2/`：

- `continuous.mp4`：连续慢放版，480 个源帧全部保序呈现，无新增 hold。
- `hold.mp4`：同音轨的停帧对照版，1,030 帧，仅用于比较画面策略；不是旧 N16 的声音顺序复刻。
- 对应 MKV：H.264 + 无损 PCM，用于核对音轨；MP4 使用 AAC，不能宣称其音频字节等同 PCM。
- `combined.wav`：两版共用的完整音轨，约 34.301375 秒。
- `timeline.json`：完整源帧到输出 PTS 映射、页面框、音轨区间、输入及成片哈希。

提交到 Git 的是代码、文档和可审阅的小型证据；完整原视频、录屏及语音文件保留本地，未塞入 Git。仅克隆仓库不包含这些媒体。

## 复现

从仓库根目录执行，依赖项目 `.venv`、FFmpeg 和分析用 NumPy（新环境可安装 `pip install -e '.[test,analysis]'`）。下列 output/report 必须是新路径，程序拒绝覆盖旧版本。

```sh
.venv/bin/python -m mlvideo.reference_motion \
  --source evidence/jttw-analysis-20260917/source.webm \
  --recording '/Users/tedczj/Desktop/录制于 2026-09-17 01.11.30.mp4' \
  --recording-crop 2560:1440:0:80 \
  --output evidence/jttw-analysis-20260917/motion-rerun

.venv/bin/python -m mlvideo.continuous \
  --manifest evidence/jttw-analysis-20260917/experiment-v2.json \
  --output evidence/jttw-analysis-20260917/ab-rerun

.venv/bin/python -m mlvideo.continuous_verify \
  evidence/jttw-analysis-20260917/ab-rerun \
  --report evidence/jttw-analysis-20260917/ab-rerun-checks.json

.venv/bin/python -m pytest tests/unit tests/integration/test_continuous_experiment.py -q
```

本目录 `experiment-v2.json` 是输入清单的备份；其相对路径以 `evidence/jttw-analysis-20260917/` 为根，恢复时先复制到该媒体目录。每页 evidence PNG 指向实际源画面，字幕出现/消失帧见 `page-transitions.json`。这里的黑白文字模板检测仅用于此已知字幕页的边界取证，仍需视觉核对，不是通用 OCR。

获取原片及准备无损短样的实际命令（目标不存在时执行）：

```sh
.venv/bin/yt-dlp --js-runtimes node --no-playlist --no-cache-dir \
  -f 'bv*+ba/b' --format-sort-force -S res,fps --write-info-json \
  -o 'evidence/jttw-analysis-20260917/source.%(ext)s' \
  'https://www.youtube.com/watch?v=73XAgADpBqY'

ffmpeg -v error -i evidence/jttw-analysis-20260917/source.webm -map 0:v:0 -an \
  -vf 'trim=start_frame=720:end_frame=1200,setpts=PTS-STARTPTS' \
  -fps_mode passthrough -c:v libx264 -crf 0 -preset fast \
  evidence/jttw-analysis-20260917/excerpt-lossless.mkv

ffmpeg -v error -i evidence/jttw-analysis-20260917/source.webm -vn \
  -af 'atrim=start_sample=1153152:end_sample=1921920,asetpts=PTS-STARTPTS' \
  -c:a pcm_s16le -ar 48000 -ac 2 evidence/jttw-analysis-20260917/excerpt-exact.wav
```

原片和录屏最初均以 Whisper small + word_timestamps 做过真实识别，结果在本地 `source-asr.json` / `recording-asr.json`。运行环境随后被删除，因此当前不声称 Whisper/CosyVoice 可重新执行。中文使用 `say -v Tingting -o zh-N.aiff '对应译文'`，再由 FFmpeg 转成 stereo/48 kHz/pcm_s16le；不裁剪或变速。已生成 WAV 及哈希仍在本地，时间线重跑不依赖已删除的模型。

## 检查与验收

| 项目 | 状态及依据 |
| --- | --- |
| 实现 | 独立实验 CLI 完成；尚未集成生产 N16/N17/N18 或 Studio |
| 自动测试 | `unit-tests.log/xml`：106 passed；`integration-tests.log/xml`：1 passed |
| 风格检查 | 新增 Python 文件的 Ruff check 通过；`git diff --check` 通过 |
| 原片血缘 | `source-lineage.json`：480 个无损截取帧的 YUV MD5 全部匹配 |
| 真实媒体 | `media-checks-v2.json`：全量帧与音频解码，480 vs 1030 帧，严格递增 PTS、两版 PCM 相等、每段中英文完整、MP4 全量解码 |
| 字幕几何 | 三页均在 1920×1080 内，中文在当前英文下方且无重叠；完整框在 `experiment-summary.json` |
| 视觉抽查 | 已看三页源帧、连续版全段 contact sheet、中文结束后静音帧、对照版停帧及第三页；未做所有输出帧的人工视觉审核 |
| 人工试听 | REVIEW；本轮没有获得用户对 Tingting、原声衔接、音画关系的试听通过 |
| 正式验收 | REVIEW；未自动升级，不代表全片质量或生产上线 |

`chinese-gap.png` 为中文结束后的一秒静音区间，中文仍随源英文页保留。`continuous-page3.png` 为第三页，`hold-page2-v2.png` 为清晰停帧。v1 曾选中转场叠影作为停帧（`hold-page2.png`），v2 改选组内带字幕的清晰帧 310/410；v1 保留为旧版本，最终参考 v2。连续版两次输入映射相同，声音未改。

实际像素比较使用 H.264 逐帧中央 ROI 缩略图 MAE 容差，不是有损成片像素完全相等。Matroska 毫秒时基导致最多约 0.5 ms 的帧时间舍入误差；最后时长允许 2 ms 容差。原片已有的静态帧仍可能静止，零新增 hold 不等于每帧都有运动。慢放的动作速度、英文嘴型/音效偏移和背景音乐在中文区间中断仍需人工评估。

资源：本轮不删除旧素材或共享存储；结束阶段系统可用约 2.6 GiB。NAS 重新挂载后确认模型父目录缺失，用户说明误删；本试制未擅自重新下载大型模型。
