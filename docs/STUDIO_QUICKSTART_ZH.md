# 系列角色配音工作台：使用与升级

## 重要边界

这是用户上传源码上的增量实现，不是重写一个项目。保留原 MySQL 8.4、CLI、不可变媒体执行和 CosyVoice3 worker。新代码提供本机管理页，不是可直接暴露到公网的多用户服务。

自动跨集声纹匹配尚未实现。当前“局部分组建议”只在同一分段版本中，将已标注的局部声学簇与角色建立提示关系，遇到冲突不建议、不会自动确认。无需使用建议功能也能完成整个手工流程。

## 1. 更新前备份

停止原来的媒体任务。使用**旧代码和原来的配置**先完成一次备份，备份必须在 data_root 外：

```bash
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json \
  backup /absolute/path/to/backups/before-series-studio
```

接着在现有源码工作目录应用补丁或替换交付包内的源码文件。保留 `.env`、`config/local*.json`、数据目录、模型环境、原来的 `.git`。不要把示例配置当成完整私有配置覆盖。

## 2. 安装管理页依赖，执行新增迁移

```bash
.venv/bin/python -m pip install -r requirements.studio.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json migrate
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json studio --port 8787
```

`migrate` 会核验已有迁移的摘要，增量创建 002 的 `studio_*` 表和 003 的独立片段备注表。已有执行、媒体路径及 artifact ID 不迁移、不重新编号。启动前会检查 migration 003 是否已完成。

浏览器打开 `http://127.0.0.1:8787`。`studio` 默认启动一个独立 `studio-worker` 进程，页面退出时停止子进程；模型进程仍受原引擎超时和进程组管理。也可分别启动：

```bash
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json studio --no-worker
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json studio-worker
```

不支持 `--host 0.0.0.0`。跨设备访问、多用户认证、反向代理部署不在本版验收范围内。页面没有 CDN、外部前端脚本或新增遥测；原项目下载/模型节点的外部请求仍由已有配置决定。

## 3. 沿用模型配置

必须保留实际部署过的 `config.models`：

| 环节 | 配置项 | 说明 |
|---|---|---|
| URL → 原声分段 | `N07/whisper` | 现有 Whisper + VAD 环境；本版不下载模型替你补齐 |
| 中文翻译建议 | `N09/codex` | 可选；也可以直接在页面手动填译文 |
| 克隆配音 | `N11/cosyvoice3_zero_shot` | 现有 CosyVoice3；创建声音配置时固定部署信息 |
| 中文回识别 QA | `N12/audio_qa_asr` | 可选；未配置时只运行音频结构 QA，并保持人工复核要求 |
| 字幕及成片 | `N17/bilingual` | 已许可、覆盖中文的字体路径及实际哈希 |
| 局部声学分组 | `N07/speaker_diarization` | 可选，prepare_settings.diarization=true 时才调用 |

`config/studio.example.json` **只是可选增量字段示例**。将其中 `studio` 合并进已有配置，不覆盖数据库、模型和 data_root。

默认 `caption_mode=asr`，即从识别结果构造候选字幕，并明确记录来源，不声称是视频原有字幕。硬字幕视频要继续使用原项目已验证的 `caption_mode=ocr` 和 ROI/布局参数；OCR 服务仍通过原 `N06/captions` 配置。`caption_mode=soft` 适用于原策略支持的软字幕。

下载按可获取的最高分辨率选择，同分辨率再优先帧率，不再限制 1080p。工作台默认使用 `N04/original`：登记与源文件逐字节相同的视频、提取完整 48 kHz 双声道 PCM，并记录原始帧时间戳；不转换帧率、不做首尾补帧、不生成整片 FFV1 预处理中间文件。音频长于画面时保留音频尾部。最终合成依照原始时间戳，仅在中文配音需要时停帧；原文件始终保留。最终母版仍使用 FFV1，仍需预留成片空间。PyAV 依赖用于按时间戳写入母版。

尚未产生分段版本的旧下载任务，在重试时会重新按最高分辨率获取并记录新源文件绑定，保留旧素材和执行记录；已按新规则下载的源文件在后续重试中复用。已有分段版本的源文件不会自动替换，需新建视频条目。显式配置的 `excerpt` 仍是单独的预览截取流程；旧 CLI `N04/ffmpeg` 保留历史规范化行为。

对于 Little Fox 原片烧录的英文字幕，可沿用：

```json
{
  "studio": {
    "prepare_settings": {"caption_mode": "ocr", "roi": [0, 0.5, 1, 1], "stride_frames": 25},
    "render_settings": {
      "font_size": 24,
      "preserve_source_english": true,
      "footer_height": 0,
      "source_subtitle_box": null
    }
  }
}
```

遵守根目录 [spec.md](../spec.md)：输出尺寸与原片一致，`footer_height` 只能为 0，不能新增黑边或字幕底栏。中文字幕紧邻当前英文字幕，优先下方、空间不足时上方。英文位置变化时使用逐段/逐帧实际框，不能合并全片字幕框作为统一锚点；无可靠框则待复核，不猜位置。`source_subtitle_box` 仅用于确实固定的已确认像素坐标 `[x1,y1,x2,y2]`。本版没有新增去硬字幕/口型同步能力。

## 4. 完成第一集

1. 创建系列，例如品牌 `Little Fox`，名称 `Rocket Girl`。另建 `Journey to the West`，两个系列角色隔离。
2. 新建你已经认识的角色，包括旁白。无需填写审核人姓名；操作以本机用户 `local-user` 记录。
3. 导入 YouTube 单视频链接。工作台登记任务，后台下载最高分辨率原片、提取音频、执行 ASR/VAD 和字幕处理后生成分段；页面刷新可查看进度。模型或工具缺失会显示失败，不伪造分段。
4. 在审核页播放原声或前后文，选择角色，修改英文和中文并保存。可以多选后批量标角色。未知、重叠片段不能进入完整配音计划。
   “译文 / 备注”包含两个独立输入框。点击“异步生成译文”后，结果自动填入译文框；手动修改优先，译文随标注保存。备注停止输入 350 毫秒后自动保存，离开输入框立即保存，无需确认；可清空，保存失败会显示原因并保留本机草稿。备注不参与翻译、配音或角色确认。分段未改变时，新版本继承备注并记录来源；拆分或改变文本/边界时旧备注保留在旧版本。
5. 边界不合适时可修改、拆分或合并相邻段；ASR 漏句可通过“补录片段”添加。会生成新分段版本；未改变的片段携带原标注来源，改变的片段需要重新确认角色/译文。
6. 为每个主要角色选择一段连续、干净、1–30 秒的原声作为候选。允许把截取范围延伸到相邻同角色台词，但不能混入未确认角色，也不能用静音重复凑时长。
7. 到角色页试听参考，逐项确认身份、准确英文转录、干净程度和词尾，再批准。之后创建声音配置，生成中文测试音。使用短词、普通句、长句和问句分别测试；测试生成本身不代表音色合格。
8. 实际试听后发布声音配置，成为该角色默认声音。每个配置首版固定一条参考；角色参考库本身可有多条。
9. 完成整集台词的角色与中文后，冻结计划。后台核对 ASR/VAD 语音覆盖及安全切点，生成固定的 UtteranceSet/TranslationSet。如果有遗漏或无法安全分段，会失败并保留原因，需要改分段/边界后新建计划。
10. 计划材料化完成后，整集生成或逐句重做。试听后“选用”指定的音频版本；生成不会自动改选用。全部选用后合成，生成新的 MP4 候选，原始版本保留。

成片的最终字幕时序、画面、内容、声音听感仍是 REVIEW，不自动假装完成整片人工验收。

## 5. 接入你已经完成的处理结果

不必重新下载或重新识别。在导入对话框展开“已有源视频”，填写 `asset_sha512` 和 N02/source 的 `source_artifact_id`，创建视频条目。

随后在本集页面点“接入已有分析”，填写这六个明确 artifact ID：

- audio、video、canonical：来自同一 N04 规范化执行；
- speech、vad：来自同一 N07 ASR/VAD 执行，且 speech 绑定上面的 audio；
- captions：当前素材的 CaptionTrack。

可以通过原 CLI `history ASSET --kind execution` / `history ASSET --kind artifact` 查询；API `/api/assets` 和 `/api/assets/{sha512}/artifacts` 也提供只读列表。不能只填文件名或直接传磁盘路径。仅有 speaker_bank 而没有完整视频规范化结果时，还不能直接合成整片；先绑定已有完整分析或运行新分析。

## 6. 版本、重试与暂停

- 改标注：新 annotation，不覆盖旧记录；旧页面提交返回 409。
- 改分段：新 revision；作用域变化会明确处理，不能只按片段编号串接。
- 改参考：建新候选；不会改旧声音版本。
- 改默认声音：发布新 profile；已有冻结计划不受影响。
- 重做某句：新 job + 新 N11/N12 execution；固定译文和模型输入版本，不命中结果缓存。
- 选用新音频：新 selection；历史成片保持不动。
- 合成：入队时复制选用快照，后续点击其他版本不改变已排队任务。

等待人工时只有持久状态，不挂着 worker 等点击。中断任务不会自动重复调用模型。重新启动后显示 INTERRUPTED；必要时先用原 CLI 的 `status ASSET`、`recover ASSET` 确认/恢复孤立执行，然后在页面建立新重试。

备份在原媒体锁之外也持有短目录锁，包含 studio 表；恢复旧备份时允许缺少新表数据，新增表保持空。迁移是增量的：退回旧代码可以继续读取旧媒体执行；不要在新工作台运行时使用旧代码备份，否则旧备份实现不包含 studio 表。

## 7. 测试命令

```bash
# 原项目无模型回归，先生成原有测试媒体
PYTHONPATH=src .venv/bin/python scripts/make_fixtures.py
PYTHONPATH=src .venv/bin/python -m pytest tests/unit tests/integration/test_phase2_media.py -q

# 新工作台协议/API/媒体链路测试
.venv/bin/python -m pip install 'httpx>=0.28,<1'
PYTHONPATH=src .venv/bin/python -m pytest tests/studio/test_catalog.py tests/studio/test_api.py tests/studio/test_flow.py -q

# 真实 MySQL 验收：只允许新建的独立 *_studio_acceptance 库与目录
PYTHONPATH=src .venv/bin/python scripts/test_studio_mysql.py \
  --config config/local-studio-acceptance.json
```

新测试使用的 SQLite 仅在 `tests/studio/sqlite_protocol.py` 内，是 SQL 协议测试适配器，不是生产支持的数据库。`test_flow.py` 的 N11 是显式测试替身：生成提示音来测试请求、采样时长和版本血缘；并非真实中文配音。真实 MySQL 脚本同样保留该 TTS 测试替身，实际音色请用上面的页面流程独立验收。

本次具体已执行/未执行清单见 `verification/STUDIO_IMPLEMENTATION_REPORT_ZH.md`，不能将测试数量理解为克隆质量评分。

在 macOS 上执行自动化媒体测试时，测试脚本不会假定 Linux 的 DejaVu 字体存在。可先设置 `MLVIDEO_TEST_LATIN_FONT` 为本机实际存在、具有英文标点字形的 `.ttf` 文件路径；正式中文渲染仍使用你配置的 `N17/bilingual` 中文字体。测试与运行使用的字体都不随本交付分发。

## 当前分支校对记录（2026-09-14）

交付包中的 `verification/` 保留原作者历史测试；当前 `main` 的应用、门禁修复、真实 MySQL/浏览器复验及未验收项见 [本机审查报告](STUDIO_REVIEW_20260914_ZH.md)。更换原声/分析绑定会要求重新确认标注；参考截取会拒绝人工分段未覆盖的 ASR/VAD 讲话。

工作台每 5 秒检查任务变化并自动更新页面；填写文本、未保存台词、打开审核弹窗或播放媒体时暂缓整页更新。数据库断线显示明确提示，恢复后自动重连，RUNNING 任务不会自动重跑。
