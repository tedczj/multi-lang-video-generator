# 上游源码参考与复用边界

> 历史参考，2026-09-11 补充说明：本文原文来自 [video_interleave_v2_design_and_skeleton.zip](../sources/video_interleave_v2_design_and_skeleton.zip)，内部路径相对于包内 `video_interleave_v2/`。实现/测试状态均指该参考包，未在本次更新中重跑；当前 MySQL 串行方案见 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和 [文档入口](README_ZH.md)。下方保留原始内容。

本文件区分**直接调用组件、包装少量逻辑、仅参考算法/接口**。附带骨架代码是本次新写的实现，没有把这些仓库整体搬入。极短摘录用于定位；标注“新写示例”的片段不是上游原代码。上游未在本环境全量安装/运行。

## 1. 版本锁定

已解析到完整commit的阅读基准：

| 项目 | 已核查commit |
|---|---|
| yt-dlp | `bbc809a1161d3bfca51fa36f59dda35556ee85a0` |
| video-subtitle-remover | `e109b9ddc1d0e8f153199dfa05c1d767546906d8` |
| video-subtitle-extractor | `85746f7df5bf85978fd05f3ca6ce66e321a87a72` |

其余条目是本次读取的main/master/官方文档，尚未取得可靠固定commit。生产锁定时必须记录真实`git rev-parse HEAD`、相关文件SHA512、依赖锁和权重revision；**不能把下面的移动分支地址直接当作生产运行版本**。本次核查commit不等于已完成平台兼容性测试的推荐发行版。

## 2. 按功能列出源码和摘录

### R01 / R02 — yt-dlp：直接使用下载器，不复刻站点提取逻辑

仓库：[yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)。

源码：[yt_dlp/YoutubeDL.py（固定commit）](https://github.com/yt-dlp/yt-dlp/blob/bbc809a1161d3bfca51fa36f59dda35556ee85a0/yt_dlp/YoutubeDL.py)，关注`YoutubeDL`的提取、下载及postprocessor接口。最终路径依据官方README的`after_move`阶段，不依据文件名猜测。

极短官方命令摘录：

```text
--print after_move:filepath
```

直接复用下载、格式选择、音视频合并和字幕下载。新开发的是隔离的attempt目录、版本及日志、实际文件探测、SHA512登记、失败历史保留、缓存政策和URL输入限制。不要复用下载archive命中作为“节点重试成功”。

实际调用已写在`vidflow/acquire.py::ytdlp_argv/download`，但尚未联网运行。EJS及JS runtime依赖见[官方EJS文档](https://github.com/yt-dlp/yt-dlp/wiki/EJS)；插件自己的环境版本也需记入执行回执。

### R03 — VSE：参考提取、过滤和合并流程

源码：[backend/main.py（固定commit）](https://github.com/YaoFANGUK/video-subtitle-extractor/blob/85746f7df5bf85978fd05f3ca6ce66e321a87a72/backend/main.py)。入口`SubtitleExtractor.run`；检查生成字幕、过滤水印/场景文字的调用。

实际存在的交互片段（定位无头阻塞点）：

```python
user_input = input(tr['Main']['checkWaterMark']).strip()
```

参考其帧提取、OCR及字幕合并；改为配置驱动，并保留截图、框和时间信息。不能只保留SRT，也不能继承交互input阻塞和执行完清理掉关键中间证据的策略。若只包装未修改入口，`stdin=DEVNULL`虽然避免永久等待，也可能使其失败，**并不会自动把交互程序变成可用无头程序**。

输出转为`captions.v1`；原始字框/候选结果保留raw，合并策略参数和代码版本入manifest。不要把OCR结果覆盖回同一个sub.srt路径。

### R04 — VideOCR：CLI包装备选

源码：[CLI/videocr_cli.py](https://github.com/timminator/VideOCR/blob/master/CLI/videocr_cli.py)，入口`main`与`save_subtitles_to_file`调用。

极短源码摘录：

```python
from videocr import save_subtitles_to_file, utils
```

可参考已有CLI参数化和字幕提取调用；核查其中置信度、相似度、合并间隔、区域和抽帧参数。新开发标准字框/证据导出、边界精化和插件回执。不能把一种引擎的confidence与另一种引擎直接同阈值排名。该条未锁定完整commit，应先固定版本再接生产worker。

### R05 — VSR：独立清理画面worker

源码：[backend/main.py（固定commit）](https://github.com/YaoFANGUK/video-subtitle-remover/blob/e109b9ddc1d0e8f153199dfa05c1d767546906d8/backend/main.py)。重点`SubtitleRemover.propainter_mode`、`video_inpaint`、`sttn_auto_mode`、`run`、`merge_audio_to_video`。

连续掩膜分组调用的极短摘录：

```python
continuous_frame_no_list = sub_detector.find_continuous_ranges_with_same_mask(sub_list)
```

可复用修复执行与连续区间处理，但生产worker应显式接收本项目MaskTrack，并保留处理掩膜证据。没有sub_areas时的全屏行为不能作为默认；`sttn_auto_mode`是对指定区域直接重绘，不代表自动精确选择所有字幕。

最终仅消费清理后画面，重新校验帧数/PTS。原音由本项目另行保管，不依赖其`.aac`临时音轨合并分支。底层算法/权重许可证分别检查，尤其ProPainter见R16。

### R06 — VSR检测模块：框、时序、镜头分组参考

源码：[backend/tools/subtitle_detect.py（固定commit）](https://github.com/YaoFANGUK/video-subtitle-remover/blob/e109b9ddc1d0e8f153199dfa05c1d767546906d8/backend/tools/subtitle_detect.py)。函数`detect_subtitle`、`find_subtitle_frame_no`、连续区间/相同掩膜分组、镜头分割相关方法。

极短入口摘录：

```python
class SubtitleDetect:
```

可参考ROI过滤、逐帧/抽样帧字框记录及区间归一化。新增对短字幕的边界精化、图像resize逆变换、MaskTrack标准化和帧索引统一。上游框顺序与本项目XYXY不同，适配时显式转换并做单元测试，不能转错位置后依旧报告“修复成功”。

### R07 — WhisperX：语音对齐和说话者映射参考

源码：[whisperx/alignment.py](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py)，关注`load_align_model`、`align`；另见[whisperx/diarize.py](https://github.com/m-bain/whisperX/blob/main/whisperx/diarize.py)。

极短源码标识：

```python
def align(
```

使用对齐/词级时间和说话者信息构造SpeechTrack。新开发所有原始语音保护区、置信度门禁、字幕归句和帧级安全切点。某些对齐失败路径返回原分段时间，不能把“返回了一条时间”当作精确对齐已经成功。重叠对话仍需显式合并/复核策略。

### R08 — YuzuMarker.FontDetection：只用候选识别

源码：[demo.py](https://github.com/JeffersonQin/YuzuMarker.FontDetection/blob/master/demo.py)，函数`recognize_font`。

极短实际摘录（变量拼写沿用上游）：

```python
indicies = torch.topk(prob, 9)[1]
```

参考字体候选分类，不把top1当成原字体的证明。新增多字幕联合重渲染匹配、字号/轮廓/描边/阴影拟合、中文字体映射，以及exact/approximate/unknown状态。该模型候选集合、字体资源和授权仍需核查。

### R09 — pysubs2：直接使用字幕数据对象与读写

源码：[pysubs2/ssafile.py](https://github.com/tkarabela/pysubs2/blob/master/pysubs2/ssafile.py)、[ssaevent.py](https://github.com/tkarabela/pysubs2/blob/master/pysubs2/ssaevent.py)、[ssastyle.py](https://github.com/tkarabela/pysubs2/blob/master/pysubs2/ssastyle.py)。关注`SSAFile`、`SSAEvent`、样式和保存功能。

新写的调用示例，不是上游摘录：

```python
subs = pysubs2.SSAFile()
subs.events.append(pysubs2.SSAEvent(start=start_ms, end=end_ms, text=escaped_text))
subs.save(output_ass, format_="ass")
```

不改其底层读写库；自己开发双语布局、位置计算、换行和事件映射。输出ASS精度不足以替代采样级音频时间线，先计划媒体时间，最后按字幕格式精度转换。

### R10 / R11 — libass与fontTools：渲染、字形检查

[libass仓库](https://github.com/libass/libass)为ASS/SSA渲染器；[fontTools TTFont文档](https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html)提供字体内容与Unicode映射访问。

新写的字符覆盖检查示意：

```python
font = TTFont(font_path)
coverage = font.getBestCmap() or {}
missing = {c for c in text if not c.isspace() and ord(c) not in coverage}
```

复用渲染和字体读取，不自己写字形光栅化。新增合法字体目录、文件哈希、中文fallback审计、实际渲染框检查。含特殊变体选择符、组合字符时不能只用此简化cmap检查代表完整shaping质量；需最终渲染核验。

### R12 — FFmpeg：媒体执行，不承担业务调度

来源：[FFmpeg Filters Documentation](https://ffmpeg.org/ffmpeg-filters.html)。定位`trim/atrim`、`setpts/asetpts`、`tpad`、`concat`、`subtitles`以及`silencedetect/freezedetect`章节。复用已有命令行操作，不修改FFmpeg C源码。

本项目新写的冻结滤镜片段：

```text
trim=start_frame=224:end_frame=225,
setpts=PTS-STARTPTS,
tpad=stop_mode=clone:stop=74,
setpts=N/(25*TB)
```

这表示以原帧224生成75帧定格；实际参考命令还显式设置输出25fps/CFR。测试发现单帧裁剪后自动帧率/PTS处理可能与预期不一致，因此必须检查实际帧数和顺序，不能仅凭filter表达式推断成功。

新增gap-first计划、声画统一映射、完整性验收、原音保护和字幕业务规则。`-shortest`不能用于掩盖不同流长度；不能把低音量当无语音。

### R13 — QCTools/qcli：基础分析可选

来源：[bavc/qctools](https://github.com/bavc/qctools)。官方CLI调用片段：

```text
qcli -i video.mov
```

可用分析报告作为质量证据；不把其中某一指标阈值直接当全业务最终PASS。新增期望定格/原片静止排除、时间线/字幕/语音语义校验。不是首版必装依赖；骨架当前使用FFmpeg完成基础媒体验证。

### R14 / R15 — SQLite与entry points：直接使用机制

[SQLite WAL官方文档](https://www.sqlite.org/wal.html)、[Python entry points规范](https://packaging.python.org/en/latest/specifications/entry-points/)。

本项目实际调用：

```python
importlib.metadata.entry_points(group="vidflow.strategies")
```

复用事务/数据库与插件发现机制；新开发资产内执行版本、不可变文件发布、日志outbox投影、显式artifact绑定和无条件重试。不把本地WAL数据库放到多机共享网络文件系统中并发使用。

### R16 — ProPainter许可证门禁

来源：[sczhou/ProPainter/LICENSE](https://github.com/sczhou/ProPainter/blob/main/LICENSE)。其限制需要单独审查，不能仅按VSR外层仓库许可证作结论。未在本包分发该实现或模型。

## 3. 不作为主基座的应用

pyVideoTrans、VideoLingo等应用的翻译/TTS集成和字幕处理可以作为后续适配代码的阅读材料，但不纳入本骨架的运行依赖。这里不再把“接了很多LLM/配音模型”计为主框架复用优势，也不继承其替换配音/对齐原时长的核心假设。

## 4. 接入上游时必须额外记录

每一个worker部署记录仓库、完整commit、工作树是否修改、patch或源码快照、相关文件SHA512、Python依赖锁、二进制版本及模型revision。不得用本文件中的阅读日期替代运行版本。每次调用的回执指向这份不可变部署记录。
