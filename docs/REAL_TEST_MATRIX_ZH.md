# 单元测试与真实环境集成测试矩阵

> 历史参考，2026-09-11 补充说明：本文原文来自 [bivideo_design_and_reference_v2.zip](../sources/bivideo_design_and_reference_v2.zip)，内部路径相对于包内 `video_pipeline_v2/`。实现/测试状态均指该参考包，未在本次更新中重跑；当前 MySQL 串行方案见 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和 [文档入口](README_ZH.md)。下方保留原始内容。

## 1. 测试状态定义

`implemented+executed` 表示本包测试代码已实际执行；`implemented+conditional` 表示已有测试入口，但需真实环境变量/外部模型；`planned` 表示详细验收方案，尚未实现自动化。**提示音不是语音模型，固定字幕不是 OCR，命令参数单测不是 YouTube 下载。** 精确帧/采样比较只对无损中间结果成立，不对 H.264/AAC 字节做等同承诺。

本次确切数量、环境及输出见 `../verification/TEST_REPORT_ZH.md`；每次重新执行使用新输出目录，保留 JUnit 和日志。

## 2. 已实现的单元与组件测试

| 文件 | 覆盖 | 验收重点 |
|---|---|---|
| `test_core.py` | 源 bytes 聚合、不同 bytes、输入哈希、路径安全、失败产物、参数与日志、显式选择 | 相同源只一个 asset；每次 attempt 新版本；绝不篡改旧输出 |
| `test_core.py` | 相同参数强制重试、策略 A/B、同一后续消费者 | 两次 PLUGIN_INVOKED；结果内容相同也有不同 producer；B 不需修改下游 |
| `test_core.py` | 多线程同节点分配 12 次 | 版本 1..12 唯一，日志序列完整，不能 MAX(version)+1 无锁竞争 |
| `test_core.py` | 输出篡改、伪造引用、跨视频输入、非法路径、失败输出格式 | 拒绝消费，保存失败版本和证据，不产生可选成功产物 |
| `test_timeline.py` | 长/短/零空档、尾段、多语言、子帧语音边界 | `max(0, pre+audio+post-gap)`，原片不剪掉，译音不叠原讲话 |
| `test_timeline.py` | 400 个种子随机案例 | 帧数/采样数守恒；原语音/译音顺序及无重叠 |
| `test_timeline.py` | 30000/1001，连续 1000 次插入 | 用累计补帧映射采样，不累加每段舍入误差；误差不超 0.5 sample |
| `test_acquire.py` | URL 规范化、单视频限制、参数、防结果缓存 | 单页解析不被 query 注入；无 download archive、无续用旧输出 |
| `test_worker.py` | 实际本机子进程、随机 nonce、超时 | 重试真实启动新的进程请求；超时终止进程组并保存证据 |

运行：

```bash
python -m pytest -q --junitxml=verification/new-unit-and-integration.xml
```

测试 fixture `fixture_a`/`fixture_b` 的目的只是验证可替换契约，不用于报告 OCR 精度。

## 3. 已执行的真实 FFmpeg 集成测试

### I-MEDIA-01：短空档与尾部补帧

`test_media_real.py` 实际调用 FFmpeg 生成/解码/合成媒体。

输入：10 秒、25 fps、160×96、250 帧；48 kHz 单声道 PCM 480000 个采样帧。第一段原声 2–5 秒，下一段 8–9 秒；对应译音分别 4 秒、1 秒；前后各 1 秒。原声和译音使用不同频率的合成提示音。

独立期望：第一处补 3 秒、75 帧；尾部补 2 秒、50 帧；输出 15 秒、375 帧、720000 音频采样帧。第一段译音输出 6–10 秒；下一段原声 11–12 秒；末段译音 13–14 秒。

验证：逐 RGB 帧哈希比对全部源帧顺序与复制帧；逐 PCM 采样比对原音保存、沉默区及译音插入位置；实际成片完整解码；输出帧数及无损音频时长符合期望。

### I-MEDIA-02：自然间隔足够

输入：20 秒、500 帧；原声分别 2–5、13–14 秒，译音同上。

独立期望：没有补帧；输出仍 20 秒、500 帧、960000 采样帧；译音分别 6–10、15–16 秒；下一段原声仍在 13 秒。中文后多余空档保留，不强行剪到恰好 1 秒。

验证：所有视频帧与输入完全同序；音频按独立黄金数组验证；实际成片解码通过。

## 4. 已实现、但依赖真实环境的集成入口

### I-NET-01：真实 YouTube 下载

准备：安装 `yt-dlp[default]`、FFmpeg、官方要求的 JS runtime/EJS；使用自己上传或有权处理的短视频，包含明确原始英文音轨。网络、身份凭据由运行者提供；不能使用示例假 URL 当成功测试。

```bash
export BIVIDEO_YOUTUBE_TEST_URL='https://www.youtube.com/watch?v=<真实11位ID>'
python -m pytest -q -m network
```

现有条件测试检查一次真实下载/归档基本成功。下列扩展用例仍需新增：同 URL 再下载；多语言音轨选原声；限流/超时重试；下载失败后再次获取；媒体不同编码但视觉相同的 hash 不混淆；下载中杀进程后恢复 acquisition 证据。

本次环境无可用 yt-dlp 安装和真实网络执行条件，未下载任何 YouTube 视频；该入口未运行不计为通过。

### I-OCR-01：真实 OCR worker 接口

准备：启动/提供符合 `worker_request.v1` 的真实 worker，生成 `BIVIDEO_REAL_OCR_CASES` 指定的 JSON。见 `configs/real_ocr_cases.example.json`。真实测试数据和 worker 不含在本包。

```bash
export BIVIDEO_REAL_OCR_CASES='/absolute/path/to/real_ocr_cases.json'
python -m pytest -q -m models
```

现有入口执行真实进程并检查必要文本、不接受 `fixture_only` 结果。它不是完整 OCR 精度基准；词错率、时间/区域精度、跨策略对照仍需按下一节追加。

## 5. 生产集成矩阵：已设计、待实现与执行

### 5.1 测试素材集

建立 `goldens/manifest.json`，每条素材保存原始 SHA-512、授权来源、视频属性、干净背景、人工语句边界、字幕文字/位置/样式、说话者和真实参考音频。字体选有明确许可的本地字体，测试清单记录其哈希但本包不包含字体。使用真实英文录音和实际模型生成，不能仅靠合成正弦波替代语音质量测试。

建议最小样本 24 条，每条 10–60 秒；另加 1 条 30 分钟素材检测累计漂移/内存和断点。此数量是项目验收建议，不是已收集样本数量。

| ID | 场景与执行 | 明确验收标准 | 状态 |
|---|---|---|---|
| NET-02 | 同一 URL 两次独立 download attempt | 都真实执行下载；旧目录不复用；归并按完成文件 bytes，不按 URL | planned |
| NET-03 | 手动返回 429/超时/不可用片段 | 失败版本保留；外层 retry 生成新 acquisition；不把部分片段当成功 | planned |
| NET-04 | YouTube 提供原声与自动配音多音轨 | 保存选择证据；输出原声语言符合配置，否则 REVIEW/FAIL | planned |
| SUB-01 | 已知干净视频烧白字黑描边，OCR A/B | 两者符合完整 schema；易例文本 CER≤1%、时间误差≤2帧、漏事件0（初始阈值） | planned |
| SUB-02 | 0.2秒短字幕、逐词字幕、两行字幕、字幕换位置 | 明确捕获或标低置信；不得静默丢弃；边界证据可定位 | planned |
| SUB-03 | 画面另有路牌/幻灯片文字 | 场景文字不得加入去除掩膜；允许区域外无主动修改 | planned |
| SUB-04 | 软字幕与硬字幕文本不同 | 保留两份候选；选择有决策证据；不因有软字幕跳过硬字幕检测 | planned |
| CLEAN-01 | 对烧字素材修复，与已知干净原片比较 | 帧/PTS/尺寸守恒；易例残字 OCR 无有效词；比较掩膜内质量，报告不确定性 | planned |
| CLEAN-02 | 字幕跨镜头、移动背景、人脸后方字幕 | 掩膜不跨场景污染；人工标记明显瑕疵的样本应被 QA 定位 REVIEW | planned |
| STYLE-01 | 字体在/不在候选库、英文无中文字形 | 准确/近似/未知状态正确；中文缺字阻断发布；字体文件哈希完整 | planned |
| STYLE-02 | 长英文+长中文、表情/专名、两行分页 | 所有显示帧无越界/相互遮挡，next English 不在冻结时提前出现 | planned |
| SPEECH-01 | 英文原声含尾气声和低音量单词 | 按人工标注边界预设100ms容差验证；不得切词或将中文叠入英文 | planned |
| SPEECH-02 | 背景音乐持续但无讲话的空档 | 能利用空档；不能要求整条混音低于静音阈值才判无人声 | planned |
| SPEECH-03 | 没有字幕的额外讲话 | 仍受保护，不被中文覆盖；保留识别证据 | planned |
| SPEECH-04 | 两人重叠或没有可接受句界 | 合并安全单元或 REVIEW；不能将负间隔强转零掩盖冲突 | planned |
| MODEL-01 | 真实译文+授权音色，单词、短句、数字专名 | 独立 ASR/规则检查，数字与否定词错误0；主观音色不以单一分数宣告保证 | planned |
| MODEL-02 | 相同参数同参考重试3次 | 3个真实调用 request ID与3个attempt；即使音频哈希相同也完整保存 | planned |
| MODEL-03 | provider 超时后客户端重试 | 记录旧请求可能仍运行；使用新调用身份，不假设远端 exactly-once | planned |
| MEDIA-03 | 30000/1001 实际视频、1000段短空档 | 无损帧数按计划；累计采样舍入误差≤1sample；完整解码无错 | planned（纯规划已测） |
| MEDIA-04 | 真实 VFR、非零PTS、旋转、不同声道/采样率 | normalize生成显式映射；未经支持策略不能带错时钟进入planner | planned |
| MEDIA-05 | 长达30分钟连续处理 | 无丢尾、全部原帧/原音映射；常驻内存受预算限制；计划和实际一致 | planned |
| QA-01 | 注入漏译段/重复译段/错误音轨/字幕越界 | 全部已知注入错误不能自动PASS；定位到attempt与单元 | planned |
| QA-02 | 合法冻结与原片静止、非法额外冻结混合 | 正常场景不误FAIL；非法差异至少REVIEW；不靠freeze检测器单独判定 | planned |
| SYS-01 | 每个发布步骤杀主进程 | 旧成功产物可用，新半成品不能成为SUCCEEDED；能恢复发布或标ABANDONED | planned |
| SYS-02 | 磁盘满、权限拒绝、日志尾行损坏 | 保存可保存的错误；不虚报成功；恢复索引/日志有审计事件 | planned |
| SYS-03 | worker逃逸路径/符号链接/跨asset引用/输出篡改 | 核心全部拒绝；不能发布引用范围外产物 | partial（引用/路径已测） |
| SYS-04 | 12并发执行同node同item、多个选择分支 | 唯一版本、不可变快照；同asset输出不互相覆盖 | partial（版本分配已测） |
| SYS-05 | worker/主项目代码未提交、模型mutable alias | strict模式拒绝或标reproducibility unknown，禁止伪造固定revision | planned/partial |

表中的 CER、时间误差等数值是**初始验收阈值**，应针对素材类别校准，不能描述为已测得能力。复杂背景和字体恢复没有合理的统一数值保证；使用标注集的误报/漏报与可定位证据进行评估。

## 6. 发布门禁

发布测试输出一份 `release_gate.json`，每项包含 test_id、数据集哈希、执行版本、状态、指标、阈值来源、证据引用。必需项 SKIPPED/NOT_IMPLEMENTED 不能合成 PASS。整条AI流水线上线条件是模型与网络测试真实执行，不是参考骨架的单测全部通过。
