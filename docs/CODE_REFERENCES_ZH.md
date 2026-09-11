# 开源组件与代码参考定位

> 历史参考，2026-09-11 补充说明：本文原文来自 [bivideo_design_and_reference_v2.zip](../sources/bivideo_design_and_reference_v2.zip)，内部路径相对于包内 `video_pipeline_v2/`。实现/测试状态均指该参考包，未在本次更新中重跑；当前 MySQL 串行方案见 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和 [文档入口](README_ZH.md)。下方保留原始内容。

本表服务于新的项目骨架，不建议 fork 任一整套翻译配音应用。**“复用”区分直接依赖、独立 worker 包装、只参考算法/接口三种方式。** 下面短代码块标注“上游摘录”的来自已核查文件；标注“新增适配示意”的是本项目拟写的胶水，不是上游现成功能。代码行号会变化，因此以文件路径和函数名定位。

在线核查的是公开分支源码，没有获取到可信的固定 commit；`references.catalog.json` 中的 `resolved_commit` 保持 `null`。必须先运行 `scripts/pin_upstreams.py` 生成锁，审查锁定 commit 中的函数是否仍一致，再构建对应 worker。不能将分支名作为生产环境的代码版本。

## 1. 下载：yt-dlp

**仓库：** https://github.com/yt-dlp/yt-dlp  
**路径：** `yt_dlp/YoutubeDL.py`，`YoutubeDL.extract_info()`；README 的 output template、configuration、archive、embedding 与 dependencies。

上游摘录：

```python
def extract_info(self, url, download=True, ie_key=None, extra_info=None,
                 process=True, force_generic_extractor=False):
```

直接使用其 CLI 或 Python API；不复制 YouTube 解析器。它解决页面解析、格式选择、媒体下载和后处理入口。项目自己的 `acquire.py` 负责每次执行的独立目录、有效参数、退出状态、`after_move` 最终路径、媒体流检查和归档。

**必须新开发：** 未知 SHA 时的 acquisition 身份；下载后按文件字节归并；获取记录和节点执行记录衔接；重试重新下载；原语言音轨确认；身份/凭据脱敏。忽略用户级隐式配置，禁用 download archive 与结果复用。JS runtime/EJS 和 FFmpeg 的安装按官方依赖说明处理，而非自行实现签名解析。

**验收：** 自有测试 URL 实际下载两次；两次 acquisition ID 不同，均记录真实进程执行；得到相同媒体字节时归并至同一 asset，不要求同 URL 的两次封装字节一定相同。不能把命令构造单测称为下载成功。

## 2. 字幕提取候选 A：VideOCR

**仓库：** https://github.com/timminator/VideOCR  
**路径：** `CLI/videocr_cli.py`，参数解析及 `save_subtitles_to_file()` 调用。

上游摘录：

```python
from videocr import save_subtitles_to_file, utils
```

可以包装其 CLI，参考字幕区域裁剪、帧跳过、相似度过滤、最短字幕长度和相邻字幕合并参数。先将每次执行限定于独立 work 目录，再将原始输出转换为统一 `captions.v1` / 完整生产 `CaptionSet.v1`。

**必须新开发：** 不能只保存 SRT；需导出字框、样式截图、原始识别记录、置信度、帧时间和关联依据。没有可获得的置信度时用 `null` + `confidence_kind=unavailable`，禁止填 1 冒充可靠。合并规则及去重策略也要记录版本。不同 OCR 的事件 ID 在各自产物内有效，禁止按列表下标跨策略对应。

**验收：** 已知文字/时间/位置的烧字素材，统计文本误差、事件漏检、时差和框误差；快速字幕不得被默认跳帧/最短时长过滤悄悄丢弃。

## 3. 字幕提取候选 B：VSE

**仓库：** https://github.com/YaoFANGUK/video-subtitle-extractor  
**路径：** `backend/main.py`，`SubtitleExtractor`、`run()`、帧提取和字幕生成流程。

上游摘录（需要替换的交互点）：

```python
input(tr['Main']['checkWaterMark']).strip()
```

可参考其帧提取、OCR、重复字幕处理和字幕输出流程。不将上游临时目录命名方式继承到多视频、多次重试体系。

**必须新开发：** 改为参数化的水印/字幕区域策略；任何 stdin 交互改成明确失败或 REVIEW；每次工作目录隔离；原始证据及中间输出按执行版本发布。不要依靠给 stdin 自动发送默认答案作为长期无人值守方案。

**验收：** 关闭 stdin 运行，没有区域/水印决策时应立即返回结构化状态，不得挂起；同名不同内容的视频不得共用临时数据。

## 4. 自建 OCR worker 的底层：PaddleOCR

**仓库：** https://github.com/PaddlePaddle/PaddleOCR  
**路径：** `paddleocr/_pipelines/ocr.py`，`PaddleOCR.predict_iter()`、`predict()`。

上游摘录：

```python
return self.paddlex_pipeline.predict(
```

它提供图片 OCR 推理接口。适合已有帧索引和区域检测后直接调用，避免继承 GUI 和视频任务框架。字体推断不等于 OCR 的文字识别功能。

**必须新开发：** 视频抽帧/时间插值、跨帧合并、字幕带选择、原始 OCR 字段标准化，以及模型名称/revision/权重校验和的运行记录。VLM OCR 也输出同一契约，但额外保存提示词和原始响应；视觉模型返回的置信度不得当作经过校准的概率。

**验收：** Paddle 与另一真实 OCR worker 对同一固定素材运行；两者都通过标准契约，后续 caption audit/utterance 节点无需修改代码。

## 5. 字幕检测、掩膜与视频清理：VSR

**仓库：** https://github.com/YaoFANGUK/video-subtitle-remover  
**路径 A：** `backend/tools/subtitle_detect.py`，`SubtitleDetect.find_subtitle_frame_no()`、`unify_regions()`、`find_continuous_ranges_with_same_mask()`、`split_range_by_scene()`。  
**路径 B：** `backend/main.py`，`SubtitleRemover.video_inpaint()`、`propainter_mode()`、`sttn_auto_mode()`、`merge_audio_to_video()`。

上游摘录（文件 A）：

```python
class SubtitleDetect:
```

复用文字区域检测、连续区间分组、掩膜归一化、场景边界处理及分批修复；优先以独立 worker 运行。修复模型按许可和硬件单独选择。

**必须新开发：** 使用目标字幕证据限制可改区域；掩膜/场景/处理帧写成版本化标准结果；修复前后帧数、PTS、尺寸和范围检查。`sttn-auto` 不是“只自动删除对白”：它可跳过检测重绘选区；无指定区域也不能默认接受全屏去字。只取清理后的画面；总流程重新管理原音，不依赖其 `.aac` 临时文件和原音回填路径。

**验收：** 已知干净背景上烧字幕，同时放置需要保留的标题/路牌；检测残字并检查允许区域外是否被误改。硬字幕去除是修复估计，不能声称恢复了被遮挡背景的真实原始像素。

## 6. 语音活动、词级时间与边界：WhisperX

**仓库：** https://github.com/m-bain/whisperX  
**路径：** `whisperx/alignment.py`，`load_align_model()`、`align()`；README 中的语音重叠等限制。

上游短签名摘录：

```python
def align(
```

可直接包装音频识别和词级对齐模块，输出源音时钟上的词、讲话区间及模型证据。不让对齐模块决定视频插帧位置。

**必须新开发：** 字幕与语音多对多关联、语义单元分组、安全边界、保护无字幕讲话、重叠讲话降级策略。整条原混音中“音量低”不等于“无人说话”；下一段讲话边界要覆盖全部原始人声。

**验收：** 人工标注边界的原声素材；特别测试短词、尾音、背景音乐、连续语句和多人交叠。低置信或重叠不能强行标为安全切点。

## 7. 字体候选：YuzuMarker.FontDetection

**仓库：** https://github.com/JeffersonQin/YuzuMarker.FontDetection  
**路径：** `demo.py`，`recognize_font()`。

上游摘录（变量拼写沿用原文）：

```python
indicies = torch.topk(prob, 9)[1]
```

仅参考“截图→候选字体排序”。候选库、可用字体范围及模型训练分布限制决定它不是通用的原字体鉴定保证。

**必须新开发：** 候选原文重渲染比对、多截图聚合、颜色/字重/描边/阴影估计、中文替代字体选择、置信度和人工复核状态。选择字体文件后记录文件哈希和许可，不仅保存字体名称。

**验收：** 已知字体烧字的样本检测 top-k，再验证重新渲染的边界/字形；不存在于候选库的字体必须允许返回近似或未知。

## 8. 字形覆盖：fontTools

**项目：** https://github.com/fonttools/fonttools  
**官方接口：** https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html ，`TTFont.getBestCmap()`。

新增适配示意：

```python
from fontTools.ttLib import TTFont
with TTFont(font_path) as font:
    cmap = font.getBestCmap() or {}
missing = sorted({ord(c) for c in text if not c.isspace()} - set(cmap))
```

用 Unicode cmap 检查基础字形覆盖，不复制字体解析器。**仍需渲染检查复杂整形、字体回退和可见缺字**；cmap 检查通过不代表布局正确。

**必须新开发：** 字体文件级 provenance、中文 fallback 决策、缺字与实际渲染差异报告。包内不分发用户系统字体。

## 9. 字幕结构与渲染：pysubs2 + libass

**仓库：** https://github.com/tkarabela/pysubs2 ，https://github.com/libass/libass  
**路径：** `pysubs2/ssafile.py` 的 `SSAFile.save()`；`libass/ass_render.c` 的 `ass_render_frame()`。

上游短摘录（libass）：

```c
ASS_Image *ass_render_frame(ASS_Renderer *render_priv, ASS_Track *track,
```

直接依赖字幕格式库和渲染器，不复制渲染 C 代码。

新增适配示意：

```python
subs = pysubs2.SSAFile()
subs.events.append(pysubs2.SSAEvent(start=start_ms, end=end_ms, text=display_text))
subs.save(output_ass, format_="ass")
```

**必须新开发：** 按 ID 而非列表下标关联英中字幕；根据新时间线调整显示；英文上中文下的实际布局；避免提前显示下一句；长句分页、中文缺字/越界/重叠检查。ASS 显示精度与音频采样精度分别处理，禁止拿 SRT/ASS 时间作为音频主时钟。

**验收：** 真实渲染多行中英文；截图测量出界/交叉；中文语音跨越“正常画面→冻结”时字幕不切错句。

## 10. 空档利用、补帧、音画合成：FFmpeg

**仓库：** https://github.com/FFmpeg/FFmpeg  
**路径：** `libavfilter/vf_tpad.c`；官方 filters 文档中的 `trim/atrim`、`setpts/asetpts`、`tpad`、`concat`、`amix`、`silencedetect`、`freezedetect`。

上游摘录（`vf_tpad.c`）：

```c
frame = av_frame_clone(s->cache_stop);
```

直接调用 FFmpeg/FFprobe；上述代码仅用于理解 clone 补帧行为。视频时间线策划不能靠某个滤镜自动推导。

**必须新开发：** 完整语音间隔计算、缺口补帧、连续累计音频样本取整、源时钟到输出时钟映射、连续译音覆盖、原音/画面一致暂停恢复。不要使用 `-shortest` 来遮掩时长错误；中间产物和检查证据纳入 attempt。

**本包实际实现：** `timeline.py` 规划；`media.py` 用解码/帧复制/编码验证精确结果，不是长视频高吞吐生产渲染器。生产可改为批量 FFmpeg graph 或顺序流式合成，但输出契约和时间线不变。

## 11. 基础质量分析：QCTools / qcli

**仓库：** https://github.com/bavc/qctools  
**已核查入口：** README 的 **Using qcli**；未核查到稳定具体 CLI 源文件路径，因此不编造文件定位。

官方命令短摘录：

```bash
qcli -i [your-file-here]
```

复用基础媒体分析报告作为辅助证据；不等于一键判断译音、残字或字幕是否正确。

**必须新开发：** 将指标时间映射到 timeline；区分合法冻结/静音；生成 PASS/REVIEW/FAIL/SKIPPED；未执行的必要检查阻断自动发布。

**验收：** 注入意外黑帧、停音、额外冻结与解码损坏；合法插帧不能被误判；结果必须能定位到源片段与执行版本。

## 12. 整合软件只作局部参考：pyVideoTrans

**仓库：** https://github.com/jianchang512/pyvideotrans  
**路径：** `videotrans/task/_stage_subtitle.py`，`SubtitleMixin._process_subtitles()`。

上游短摘录：

```python
source_sub_list[i]['text']
```

仅参考双语文本上下排列与样式桥接思路。上面按下标配对的方式不适合多个字幕合为一个语句以及 OCR 策略 A/B 后的事件变更。

**不继承：** 主应用状态、全局缓存、替换式配音对齐、输出目录和隐式配置。VideoLingo、Linly-Dubbing 等的翻译/音色接口可以进一步参考，但不列作本项目剩余媒体功能的核心依赖，也不需要为了模型接口引入整个应用。

## 13. 自建内核哪些机制可以直接利用标准库

Python `importlib.metadata.entry_points` 用于插件发现，`hashlib` 用于 SHA-512，SQLite 用于索引和版本分配，文件原子替换用于单个发布文件，FFprobe 用于真实媒体属性。标准库不会自动提供跨数据库与文件系统的事务、产物级血缘或无缓存重试；这些行为由本项目 `store.py` 和 `engine.py` 明确定义。

**代码复制原则：** 优先调用稳定公共接口，次选独立进程包装，需要导出原始证据/移除交互时做小 patch。每个 patch 独立 commit，记录 upstream commit 与 patch hash。源码许可证、模型权重、字体、视频使用授权分别检查；进程隔离不是许可豁免。ProPainter 的非商业许可尤其不能被 VSR 外层许可掩盖。
