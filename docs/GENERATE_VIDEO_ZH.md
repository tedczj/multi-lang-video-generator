# 输入一个 YouTube URL，生成双语 MP4

本机已配置好模型与输出目录，可以从仓库根目录直接运行：

```bash
./scripts/generate_video.sh 'https://www.youtube.com/watch?v=73XAgADpBqY'
```

最终视频只有一份 MP4。终端成功时会打印 `output`（成片）和 `work`（渲染检查目录）。默认成片在 `/Volumes/备份/视频成片_请勿清理/`，下载、参考音、逐句配音和证据在 `/Volumes/备份/视频生成工作数据_请勿清理/`。保留工作数据才能验证来源和恢复运行；不要把运行模型或工作数据当成普通缓存删除。

可指定成片名称和固定任务目录：

```bash
./scripts/generate_video.sh 'https://www.youtube.com/watch?v=73XAgADpBqY' \
  --work '/Volumes/备份/视频生成工作数据_请勿清理/my-video' \
  --output '/Volumes/备份/视频成片_请勿清理/my-video.mp4'
```

失败或中断后，加 `--resume` 并使用原 `--work` 和 `--output`。恢复只复用输入、模型配置和产物哈希匹配的阶段；如果文件被修改会报错。新任务不能覆盖已有成片。更改输入/模型时创建新工作目录。任务状态、错误在 `job.json`，阶段检查点在 `.stages/`。

```bash
./scripts/generate_video.sh 'https://www.youtube.com/watch?v=73XAgADpBqY' \
  --work '/Volumes/备份/视频生成工作数据_请勿清理/my-video' \
  --output '/Volumes/备份/视频成片_请勿清理/my-video.mp4' --resume
```

## 实际执行什么

1. yt-dlp 下载最高可用分辨率的单个视频，保留原文件及 PTS，不做预处理插帧。
2. Whisper + 独立 VAD 识别英文，保留说话人分析建议；OCR 识别句中页面并逐帧定位字幕出现/消失。
3. 对字幕暴露出的 ASR 遗漏，重新识别对应原声音频窗口；文字提示不能单独替代音频证据。
4. Codex 生成逐词覆盖的中文脚本与角色/旁白候选；台词和紧随的 said/asked 旁白按整句安排，内部保留不同声音来源。
5. 从该视频选取对应角色的参考候选，实际运行 CosyVoice3；检查解码、削波、静音、前导能量及辅助中文 ASR。
6. 每段先英文，在英文发声结束采样点紧接中文，**零人为英中间隔**；中文后接回原片剩余音轨和自然停顿，不额外加一秒。保留所有原声 PCM，不加速或截短中文。
7. 连续慢放画面，保留原始帧顺序与尺寸；中文紧邻当前英文字幕页，并只显示该页对应句子的译文。
8. 直接编码、验证一份 H.264/yuv420p + 48 kHz stereo AAC 的 MP4，并启用 faststart。不生成 MKV 或停帧对照成片。

MP4/H.264/AAC 对应 [YouTube 官方推荐的上传容器与编码](https://support.google.com/youtube/answer/1722171?hl=en-GB)。下载原文件可能是 WebM；那是工作素材，不是第二份交付视频。

## 当前边界

- 自动脚本链路已实现，已用《西游记》整集生成 10 分 46.8 秒 MP4。实际开发中分步运行了真实模型，最终从 URL 入口断点续跑完成整片；首次全流程调度/恢复/篡改拒绝另有模型替身集成测试，不把替身测试写成真实模型结果。
- 当前面向英语音轨、画面英文字幕可可靠定位的公开视频。下载权限、模型缺失、没有可用参考、没有可靠字幕定位等情况会明确失败并保留诊断；不能承诺任何 YouTube URL 都无需处理。
- 这是目录式单命令入口，复用现有 worker；它目前不回写 Studio/MySQL 队列。工作台接入、跨集角色身份稳定性与正式全片质量验收仍未完成。
- 自动语义角色与聚类都是候选，不是人工批准的声音身份。短参考音、慢速画面、模型前导噪声/静音、ASR 不确定片段会列入 `review.json`。**零新增间隔不代表模型本身没有前导声音问题**。
- 成功退出表示生成与技术检查完成，`human_listening` / `formal_acceptance` 仍是 `REVIEW`。当前整片有 6 个低于 0.4× 的慢速句组、3 个前导能量待复核片段、1 个辅助 ASR 空结果；需要试听后决定是否重新生成这些句子。

## 新机器配置

安装主控依赖：

```bash
.venv/bin/python -m pip install -e '.[test,analysis]'
cp config/generation.example.json config/local-generation.json
```

编辑 `local-generation.json` 中模型 Python、权重、源码、PaddleOCR endpoint、字体及输出目录。需要 FFmpeg、ffprobe、Node、已登录的 Codex CLI、可用的 PaddleOCR 服务、Whisper/Silero VAD、说话人模型、CosyVoice3；模型部署说明见 [workers/README.md](../workers/README.md)。本机的实际私有配置不提交 Git。主控预留磁盘空间，缺依赖或空间不足会停止，不降级成 Tingting。

实现设计：[URL_TO_MP4_DESIGN_ZH.md](URL_TO_MP4_DESIGN_ZH.md)。本轮证据：[整体验证记录](../verification/url-to-mp4-20260918/README.md)。
