# 多语言视频流水线开发计划

更新：2026-09-12。依据：[设计大纲 plan_0.md](plan_0.md)。状态：**第一期已验收 PASS；第二期候选链路已实现、正式验收未完成；第三期待开发**。

第一期已新增 `src/mlvideo`、Compose、SQL、worker 协议和实际测试入口，运行方法见 [项目 README](../README.md)，结果与边界见 [第一期最终验收](verification/PHASE_1_ACCEPTANCE_ZH.md)。第二期当前实现与剩余项见 [三段预览报告](verification/YOUTUBE_Ye33eY4UNtY_THREE_SEGMENTS_ZH.md) 和 [第三期交接条件](kickoffs/PHASE_3_KICKOFF_ZH.md)。下文保留完整三期规格；未完成项仍须按实际证据验收。参考包结果不计入本计划验收。

## 1. 设计基线与需求编号

采用一个 Mac 主控、一个 MySQL 8.4 容器、一个写入进程和顺序执行的独立 worker。首先完成英文原声后接中文的闭环；契约保留语言字段，其他语言不计入本轮完成标准。正式开发模块暂定 `mlvideo`，与包内历史 `vidflow` 区分。

| 需求 | 必须交付的行为 | 设计位置 | 验收阶段 |
|---|---|---|---|
| REQ-01 基础设施与模块 | 轻量 MySQL、可探测的本机环境、分离主控与 worker | §2–3 | 第一期 |
| REQ-02 完整业务流程 | 导入到双语发布，每节点具有输入、输出和门禁 | §6–9 | 三期递进 |
| REQ-03 持久化与血缘 | 源 SHA-512 聚合、不可变执行版本、固定上游引用 | §3–5 | 第一期基础，后续节点沿用 |
| REQ-04 执行与重试 | 串行保护、策略替换、真实重试、基本恢复 | §4–5 | 第一期基础，第二期真实模型 |
| REQ-05 代码与日志 | 代码、参数、模型回执、原始输出、单资产日志 | §5、§10 | 三期全部 |
| REQ-06 参考与验收 | 文件/函数级参考、实际媒体和模型证据、发布检查 | §11–12 | 三期全部 |

保持大纲默认值：Codex CLI 使用 `gpt-5.6-terra`、`medium`；翻译每批最多 8 个完整语句且源文合计不超过 6,000 个 Unicode 字符；TTS 使用官方 `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`；英文后等待 1 秒开始中文，中文结束至少等待 1 秒；优先使用原片自然空档，必要时定格；英文在上、中文在下。

资源上限是待验证预算，不是实测。具体模型 worker 的设备和依赖在所属阶段先做真实探测；主控在 Mac 运行不代表所有模型能在 Mac GPU 上运行。不可用时记录阻塞及实际环境，不静默替换模型或运行后端。

## 2. 模块与交付结构

```text
multi-lang-video-generator/
  compose.yaml                       # 单实例 MySQL
  config/mysql.cnf                   # 轻量参数
  config/pipeline.json               # 固定默认值和顺序配方
  config/upstreams.lock.json         # 实际代码、镜像和模型身份
  migrations/001_initial.sql          # 按编号顺序执行
  src/mlvideo/
    cli.py / config.py               # 命令和配置
    db.py / store.py                 # PyMySQL、不可变文件
    engine.py / pipeline.py          # 运行锁、版本、顺序执行与 retry
    contracts.py / recovery.py       # 契约校验、封存清单恢复
    events.py                       # 数据库事件及 JSONL 投影
    timeline.py                     # 有理数时间线纯函数
    nodes/                          # §6 的业务节点
    adapters/                       # FFmpeg、下载、模型 worker 协议
  workers/                          # 每类模型独立环境和入口
  schemas/                          # 带版本的 JSON Schema
  tests/unit/ / tests/integration/ / tests/fixtures/
  scripts/verify_phase.py             # 所属期真实验收入口
  evidence/<verification_id>/         # 每轮验收独立目录
  docs/ / sources/                    # 文档与原始参考材料
```

基础依赖：Python 3.11+、PyMySQL、JSON Schema 校验库、FFmpeg/ffprobe。开发使用 pytest；模型库留在 worker 环境。数据库访问用明确 SQL，不引入 ORM、连接池、数据库兼容层或通用任务平台。配置只有本轮需要的选项，默认值以本计划为准。

## 3. MySQL、文件与索引

### 3.1 单实例配置

| 项目 | 初始配置 | 验证 |
|---|---|---|
| 镜像 | MySQL 8.4；第一期锁定实际补丁版本、digest、平台 | 启动报告记录 `SELECT VERSION()` 与镜像身份 |
| 端口与账号 | `127.0.0.1:3307` 映射容器 3306；一个业务库、独立应用账号 | 主控实际连接、权限检查 |
| 数据盘 | Docker named volume | 容器重启数据保留 |
| Buffer pool | `innodb_buffer_pool_size=134217728` | 读取实际变量 |
| 连接 | `max_connections=10`；主控正常使用一个连接 | 连续任务连接数不泄漏 |
| 可选组件 | `performance_schema=OFF`、`mysqlx=0`、`skip-log-bin` | 读取变量及插件状态 |
| 内存预算 | 容器先设 768 MiB | 冷启动、空闲、连续任务峰值、OOM 状态 |
| 持久化 | InnoDB；保留 redo 与默认持久化保障 | 重启、断连、文件封存恢复 |

这些组件可以按官方启动选项关闭；关闭 binary log 后仅提供暂停写入的完整备份，不提供时间点恢复。配置依据见 [MySQL Performance Schema](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-startup-configuration.html)、[X Plugin](https://dev.mysql.com/doc/refman/8.4/en/x-plugin-disabling.html)、[binary log](https://dev.mysql.com/doc/refman/8.4/en/replication-options-binary-log.html) 和 [buffer pool](https://dev.mysql.com/doc/refman/8.4/en/innodb-buffer-pool-resize.html)。

PyMySQL 连接显式设 `autocommit=True`；每条语句独立提交。该参数默认并非开启，不能省略后假设数据已经保存。[PyMySQL Connection 接口](https://pymysql.readthedocs.io/en/latest/modules/connections.html)

迁移命令取得同一运行锁，逐个检查编号和文件 SHA-512。首次建库用迁移账号，正常处理用应用账号。迁移中断后对照实际表结构和已记录校验和恢复；不宣称 DDL 文件整体原子执行。数据库凭据不写入请求、公开日志或源码。

### 3.2 必要表结构

下表是首版 SQL 必须落实的最小字段和索引。ID 用 `VARCHAR(64)`；SHA-512 用 ASCII `CHAR(128)`；时间用 UTC `DATETIME(6)`；帧数、采样数和 bytes 用 `BIGINT UNSIGNED`；结构化声明用 JSON。状态字段限定合法值，资产、执行和文件身份不可覆盖修改。外键只做引用完整性，不设级联删除。

| 表 | 主键和主要字段 | 唯一约束 / 查询索引 |
|---|---|---|
| `schema_migrations` | `version INT`、`sha512`、`applied_at` | PK version |
| `assets` | `sha512`、`source_path`、`bytes`、`created_at` | PK sha512 |
| `acquisitions` | `id`、可空 `asset_sha512`、`kind`、`retry_of`、`state`、`receipt_path`、时间 | PK id；索引 asset、retry_of |
| `runs` | `id`、`asset_sha512`、`recipe_path/hash`、`state`、`bindings_path/hash`、起止时间 | PK id；索引 (asset, started_at) |
| `executions` | `id`、`asset_sha512`、可空 `run_id`、`node`、`scope`、`version`、`strategy_id/version`、`retry_of`、`state`、`quality_status`、`request_path/hash`、`manifest_path/hash`、起止时间 | UNIQUE (asset, node, scope, version)；索引 run、retry_of、(asset, state) |
| `execution_inputs` | `execution_id`、`port`、`ordinal`、`artifact_id` | PK (execution_id, port, ordinal)；索引 artifact_id |
| `artifacts` | `id`、`execution_id`、`relative_path`、`schema_id`、`kind`、`sha512`、`bytes`、`metadata_json` | UNIQUE (execution_id, relative_path)；索引 (execution_id, kind)、(schema_id, execution_id) |
| `artifact_parents` | `artifact_id`、`parent_artifact_id` | 联合 PK；索引 parent_artifact_id |
| `selections` | `id`、`asset_sha512`、`branch`、`role`、`artifact_id`、`reason`、`created_at` | PK id；索引 (asset, branch, role, created_at) |
| `events` | `id`、`asset_sha512`、可空 `execution_id/run_id`、`seq`、`type`、`at`、`payload_json` | UNIQUE (asset, seq)；索引 execution_id、run_id |
| `releases` | `id`、`asset_sha512`、`run_id`、`render_artifact_id`、`qa_artifact_id`、`review_artifact_id`、`manifest_artifact_id`、`created_at` | PK id；索引 (asset, created_at) |

模型回执与 QA 是不可变文件，也登记为 `artifacts`：分别用 `kind=model_receipt`、`kind=qa_report`；索引通过 producer execution 关联请求的模型声明。`metadata_json` 只存可查询的小摘要，完整请求响应留文件。`quality_status` 是摘要；具体检查和人工决定以固定 artifact 为准。

所有下游读取必须联查 producer execution 的 `state=SUCCEEDED`，再校验文件摘要。部分登记的 artifact、FAILED/INTERRUPTED 执行和未封存结果都不能变成有效输入。质量需要复核时，节点可以成功产出 REVIEW 报告，流程在该门禁停止，不能将执行成功等同于质量通过。

### 3.3 文件布局

```text
data/
  .writer.lock
  incoming/<acquisition_id>/          # 未知源 SHA 时的获取记录
    request.json / receipt.json / raw/
  videos/<source_sha512>/
    source/original.bin
    acquisitions/<acquisition_id>/
    runs/<run_id>/plan.json / bindings.json / result.json
    nodes/<node>/<scope>/v000001_<execution_id>/
      request.json / runtime.json
      work/                          # 原始响应、子进程 stdout/stderr
      artifacts/                     # 已校验产物
      manifest.json                  # 封存清单
    selections/<selection_id>.json
    events.jsonl
  backups/<backup_id>/                # 暂停写入的完整备份
```

SHA-512 按最终源文件 bytes 计算，不按 URL、文件名或转码结果聚合。每次导入重新读取并建立独立 acquisition；同 bytes 共用源快照，执行身份不合并。下载完成并探测有效后才知道 asset，随后归档获取证据；归档未完成可按收据补齐，不能丢弃下载失败历史。

## 4. 串行执行、重试和恢复

### 4.1 运行锁与顺序配方

所有写入命令（导入、下载、run、retry、recover、select、迁移、备份、发布）先取得本机同一数据根的非阻塞文件锁。第二个写入命令立即报“已有任务运行”并非零退出；history/status 只读不占用运行锁。模型调用期间保持锁。

一个数据根对应一个业务库和一个主控配置，禁止不同数据根绕过同库运行锁。worker 无数据库凭据，仅写本次 work。主控退出时须终止并回收本机 worker 进程组；恢复前确认旧 worker 已停止，未知存活状态先停止恢复。远程 worker 如需使用，必须先落实运行身份、取消和完成确认，不能仅以本机锁释放判断远程推理结束。

主控被强制杀死可能来不及清理子进程。因此任何新写入命令取得锁后，先核对未结束执行和原始运行记录中的 host、PID、进程启动时间；存在未处理执行时只允许进入 recover，不启动下一次业务调用。PID 存在不等于同一进程，无法确认原 worker 已停止时明确阻塞并保留诊断；这属于本机基本恢复，不引入分布式租约。

配方运行前检查节点、依赖和固定输入是否存在、是否有环。按依赖拓扑顺序逐个等待执行完成；OCR A/B 也顺序运行。运行开始将选择解析成具体 artifact ID 并写入 plan/bindings；后续选择变化不改变本轮输入。无需并发调度或远程租约。

### 4.2 执行生命周期

1. 校验策略、参数、输入契约和文件哈希；确认需要的上游质量门禁已通过。
2. 在运行锁内取 `(asset,node,scope)` 的最大版本加一；生成新的 execution ID、request 和代码身份快照。登记 RUNNING、固定输入和开始事件；登记未完成则不调用业务。
3. 实际调用一次策略；记录 argv、有效参数、模型声明、起止时间及原始 stdout/stderr。每次使用全新 work。
4. 完整写入产物，校验 schema、业务约束和 SHA-512。文件 flush/fsync 后封存 manifest，并记录所有文件、输入、父级关系和模型回执。
5. 按清单逐条登记 artifacts、parents 和结束事件，最后标记 execution 为 SUCCEEDED；之后下游才可消费。业务失败保留 work，记录 FAILED；人工质量问题保留 REVIEW 报告。

相同文件内容可以有不同 artifact ID；父级引用是文件身份，不是内容哈希。无法细分文件依赖时，保守记录全部声明输入并标明 `lineage_kind=conservative`，不能虚构精准片段血缘。

### 4.3 三个动作

| 动作 | 是否调用业务 | 身份变化 |
|---|---|---|
| retry | 校验通过后必定重新执行，成功旧执行也可重试 | 新 execution、新版本、新目录，`retry_of` 指向原执行 |
| select | 不调用业务，明确选中历史有效 artifact | 追加 selection；旧选择和旧 run 保留 |
| recover | 不重新推理，只补全封存结果登记或标记中断 | 保留原 execution，追加恢复事件 |

retry 固定原输入和有效参数，调用当前已部署策略并记录当前代码/模型身份。若要复现旧代码，先部署旧的固定版本。篡改输入、缺失依赖不能因“无条件”跳过检查。改变参数或输入用新的 run/execute，另记来源关联。

### 4.4 故障窗口

| 中断位置 | 恢复规则 | 验证 |
|---|---|---|
| request 已落盘但未登记 / 输入未登记完整 | 核对 execution ID 和请求，补齐执行记录；没有封存结果则标 INTERRUPTED | T-RECOVER |
| worker 未完成或只写部分 work | 确认旧 worker 停止，标 INTERRUPTED；新 retry 才调用业务 | T-FAULT |
| manifest 完整、DB 只登记部分文件 | 逐文件验摘要，按固定主键补齐；已有行字段不一致则报错 | T-RECOVER |
| DB 提交回执丢失 | 重连先查同一 execution/事件 ID，确认是否完成；不重复调用模型 | T-DB |
| 最终事件已有、execution 尚未成功 | 校验清单与事件后补齐终态；未成功前不开放输入 | T-RECOVER |
| DB 成功但 JSONL 缺行或半行 | 从 DB 事件按 seq 重建投影，不生成第二份业务结果 | T-LOG |

不设计文件系统与 MySQL 跨系统事务。应用层按清单补齐，保留 InnoDB 的单语句原子性。终态不可覆盖成另一份结果；恢复不会重写已成功的 manifest。

## 5. 插件、标准结果与代码身份

每个策略声明 `node`、`strategy_id/version`、输入端口 schema、输出 schema、默认参数和 worker 部署身份。主控解析固定 artifact 为只读输入路径，传入本次工作目录。

外部 worker 统一接收 `--request <request.json> --result <worker-result.json>`。请求至少包含 `protocol_version`、asset/run/execution、完整输入引用和路径、有效参数、模型声明。响应包含状态、相对产物路径、schema、模型回执和告警。附加输出不得引用旧 work、绝对路径、`..`、符号链接或未入库远程 URL；下载的远程媒体先落盘验哈希。

每份 `ArtifactRef` 至少包含 `artifact_id`、asset、producer execution、relative_path、schema_id、sha512、bytes。业务 ID 包含其所属文档 artifact 和局部 ID；不同 OCR 策略的第 n 行不能直接视为同一字幕。输入列表显式保存 port 和 ordinal。

原始类型定义见 [数据契约参考](DATA_CONTRACTS_ZH.md)。实施时沿用其时间域、字框、语句和模型证据语义，新增严格 schema；它不是全部已实现的代码清单。当前新增约束如下：

| 契约 | 必须补足的字段 / 校验 |
|---|---|
| `TranslationBatch.v1` | batch_id、固定 UtteranceSet 引用、按顺序的 unit_ids、源文本/语言、prompt/schema 哈希、批次字符数 |
| `TranslationSet.v1` | 源批次与响应引用；每个 unit_id 恰好一次、无新增/缺失/重复；原文哈希相符 |
| `ModelReceipt.v1` | requested/resolved 模型、revision/权重摘要、实际 backend/device、worker commit/dirty、参数、请求/响应引用、耗时；不可获知字段为 null 并附原因 |
| `DubClip.v1` | unit_id、speaker_id、译文与参考音 artifact、原始音频及规范化音频、实际采样率/声道/采样帧数 |
| `QAReport.v1` | 目标 render/timeline 的固定引用、检查版本、required、status、证据及未执行原因 |
| `ReviewDecision.v1` | 固定待审 artifact、结论、复核人、时间、问题定位；新结果需新复核 |

执行 request/manifest 保存核心 git commit、dirty、源码树 SHA-512、快照路径；外部 worker 独立保存相同身份。非 Git 导出物不能编造 commit。代码快照必须能定位实际修改，模型营销名称不替代权重 revision；远端未返回真实身份时如实记录未知。

## 6. 业务节点与顺序

下表的 schema 名为业务契约；阶段一按需要先实现媒体子集。所有节点继承 §4–5 的版本、失败留证和输入固定规则。

| 节点 / 阶段 | 输入 → 输出 | 步骤与默认值 | 失败 / 复核条件 | 重试 scope；专项日志；测试；参考 |
|---|---|---|---|---|
| N01 获取 / 一 | URL 或文件 → AcquisitionReceipt | 本地快照或 yt-dlp 全单视频；下载高度上限 1080；禁用结果复用 | 下载/合流失败、流无效；未知 SHA 留 incoming | acquisition；实际下载 argv/最终路径；T-DOWNLOAD；REF-DOWNLOAD |
| N02 入库 / 一 | 完整媒体 → SourceAsset | 流式 SHA-512，归档源 bytes 和获取证据 | 读写/摘要不一致 | video；源 bytes/hash；T-STORE；REF-CORE |
| N03 探测 / 一 | SourceAsset → MediaProbe | FFprobe 记录全部流、时基、起点、旋转、色彩 | 无视频、无法完整解码；原声轨不明确转 REVIEW | video；流选择依据；T-MEDIA；REF-MEDIA |
| N04 归一化 / 一 | SourceAsset+Probe → CanonicalMedia | 保留原源；SDR CFR 工作副本、48 kHz PCM16 双声道；保存 PTS 映射 | HDR 无明确转换策略、损坏或同步无法解释则拒绝 | video；帧/采样/偏移/转换；T-MEDIA；REF-MEDIA |
| N05 字幕清点 / 二 | CanonicalMedia+Probe → SubtitleInventory+ROITrack | 先清点软字幕；硬字幕明确 ROI；无字幕用空轨并记证据 | 原声语言、字幕轨或区域不明确转 REVIEW | video；轨/ROI/存在性；T-OCR；REF-OCR |
| N06 字幕提取 / 二 | CanonicalMedia+Inventory → CaptionTrack | 软字幕解析或 PaddleOCR；保留逐帧框和原始结果；三期串行第二策略对照 | 缺时序/错框/不可读；无置信度填 null+原因 | video/ROI；模型/抽帧/合并；T-OCR；REF-OCR |
| N07 语音分析 / 二 | 原声 PCM → SpeechTrack | VAD、ASR、对齐、speaker 映射，包含无字幕讲话 | 对齐不足、交叠讲话不可安全切分转 REVIEW | video；词/边界/实际模型；T-SPEECH；REF-SPEECH |
| N08 归句 / 二 | CaptionTrack+SpeechTrack → UtteranceSet | 多对多关联；稳定 unit_id；确定最后安全整帧切点 | 无安全切点则重新归句或人工复核 | video；合并理由/引用/切点；T-UTTERANCE；REF-TIMELINE |
| N09 翻译 / 二 | 固定 UtteranceSet → TranslationSet | 连续完整语句合批；最多 8 段且 6,000 字符；Codex 指定模型/effort | 漏 ID、重复、拒绝、截断、数字语义错误；单句超限转 REVIEW | batch；批次/提示/JSONL/回执；T-TRANSLATE；REF-CODEX |
| N10 参考音频 / 二 | 原声+SpeechTrack → VoiceReference | 按 speaker 选干净语段及匹配转录；原始区间留证 | 混声、太短、转录不匹配转 REVIEW | speaker；片段/处理/转录；T-TTS；REF-TTS |
| N11 克隆配音 / 二 | TranslationSet+VoiceReference → RawDubClip | 官方 CosyVoice3；逐句 `stream=False`、原速；拼接全部返回块 | 空输出、块丢失、模型不匹配或推理失败 | unit；模型/参考音/块序/真实采样；T-TTS；REF-TTS |
| N12 音频检查 / 二 | RawDubClip+译文 → DubClip+QAReport | 完整解码、48 kHz 工作格式；文本复核、削波与截尾检查 | 漏读、错读、音色异常转 REVIEW/FAIL | unit；原/新采样数与检查；T-AUDIO；REF-MEDIA |
| N13 样式识别 / 三 | 原画面+CaptionTrack → StyleProfile | 字体候选重渲染；中文 fallback；字体文件 hash | 缺字、候选未知保留 approximate/unknown；待复核 | video/style；候选/样式截图；T-STYLE；REF-FONT |
| N14 字幕掩膜 / 三 | ROI+CaptionTrack+画面 → MaskTrack | 限定目标字幕，按镜头记录每帧区域 | 误覆盖路牌/标题、证据不足转 REVIEW | scene；掩膜及坐标变换；T-CLEAN；REF-CLEAN |
| N15 去字幕 / 三 | CanonicalMedia+MaskTrack → CleanVideo | 独立 VSR worker，仅取清理画面；重验帧/PTS | 掩膜外误改、残字、时序改变转 FAIL/REVIEW | scene；修复模型/帧/对照图；T-CLEAN；REF-CLEAN |
| N16 时间线 / 一 | UtteranceSet+DubClip → TimelinePlan | §8 空档优先；pre=post=1 秒；完整保留源序列 | 无安全切点、音频缺失、时间域混用则拒绝 | video；缺口/hold/时钟映射；T-TIMELINE；REF-TIMELINE |
| N17 双语布局 / 二，三扩展 | Timeline+英中语句+样式 → SubtitleLayout | 英文上中文下；二期明确基准字体，三期恢复样式；按 ID 配对 | 缺字、越界、遮挡或错配转 REVIEW/FAIL | video；字体/边界/字幕事件；T-LAYOUT；REF-FONT |
| N18 合成 / 一媒体子集，二完整 | Canonical/CleanVideo+Timeline+Layout+DubClip → RenderBundle | 原音画同停同走；无损母版与 H.264/AAC 预览 | 帧/采样不符、解码失败、字幕缺失 | video；FFmpeg argv/母版检查；T-RENDER；REF-MEDIA |
| N19 质量与复核 / 二基础，三完整 | RenderBundle+全部固定证据 → QAReport+ReviewDecision | 按 §9 逐项运行；人工记录翻译/音色/画面 | 必选未执行阻止 PASS；报告需绑定本次成片 | video；逐项结果/截图/复核；T-QA；REF-QA |
| N20 发布 / 三 | Render+PASS QA+有效复核 → Release | 同一固定输出集合，封存发布清单和摘要 | 缺证据、摘要变化、复核对象不同则拒绝 | video；release ID/完整引用；T-RELEASE；REF-CORE |

无字幕分支以 ASR 作为文本来源，保留 `text_source=asr`；无讲话且确无字幕则跳过翻译/TTS 并说明不适用。无硬字幕可跳过 N13–N15，字幕清点证据必须支持该判断。第二期硬字幕素材不进入正式闭环；第三期才验收去字。必需检查未实现不能写“不适用”。

## 7. 翻译与 CosyVoice3 接入

### 7.1 Codex 批次

批次构建只在完整 unit 边界切分，同时满足段数与字符数上限。超限单句不截断，回到归句或复核；上下文、术语及输出 token 预算另行记录。batch_id 绑定固定输入与分组版本，输出按 unit_id 校验后拆回各句，不能按返回数组位置配对。批次 retry 整批重新调用，下游只引用明确选择的成功批次。

下面是 worker 要生成的命令形式，输入、schema 和目录由主控准备；**本次未运行翻译**：

```bash
codex exec --skip-git-repo-check --sandbox read-only \
  --model gpt-5.6-terra -c 'model_reasoning_effort="medium"' \
  --json --output-schema translation-output.schema.json \
  --output-last-message translation-result.json - \
  < translation-prompt.txt > codex-events.jsonl 2> codex-stderr.log
```

`--json` 输出事件流，最终结构化对象单独读取；成功条件同时检查进程状态、最终 JSON Schema 和 ID/语义完整性。worker 在专用工作目录运行，声明只进行翻译，并记录有效配置，不使用上次会话恢复来冒充重试。[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)

设计时先核对本机 `codex-cli 0.153.4` 的帮助参数；模型名与 medium 也有 [GPT-5.6 Terra 官方说明](https://developers.openai.com/api/docs/models/gpt-5.6-terra) 支持。当前本机已完成第二期真实 Codex 调用，见对应验证报告；其他目标机器仍需实际验证。CLI/服务未返回真实 resolved identity 时填 null，不以请求参数伪造实际回执。

### 7.2 官方 CosyVoice3

固定模型为 `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`。读取官方 `example.py::cosyvoice3_example`，由 `AutoModel` 加载；`CosyVoice3` 的 `inference_zero_shot` 继承自 `CosyVoice`，输入由 frontend 处理。仓库原 FunAudioLLM 地址当前重定向至 QwenAudio，完整阅读版本记录在 [实现参考](IMPLEMENTATION_REFERENCES_ZH.md)。[官方示例](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/example.py)

同 speaker 的参考音和准确转录都以 artifact 固定；将模型要求的提示前缀与字幕正文分开保存。不得把提示文本混入待朗读的译文。逐句保留原始采样率音频，再规范化工作副本；拼接生成器返回的全部音频块后按实际采样帧计时，不能按字符数估算时长。

英文参考与中文目标的跨语言合成须用真实素材验证内容和音色；不要仅凭接口能调用宣布克隆质量合格。模型环境先做单句冒烟、实际设备报告，再接入多句闭环；CPU/CUDA/MPS 支持与速度只报告本机实测。

## 8. 空档优先时间线与音画

### 8.1 三种时钟

源容器 PTS、canonical source、输出时间线分别保存映射。帧索引从 0 开始，区间左闭右开；PCM 时间按 sample frame，双声道同时的两个采样算一帧。计划使用整数与有理数，ASS 时间只用于最终显示。

N04 的归一化必须记录 CFR 目标、重复/丢弃帧、旋转与源音偏移；音画完整性基准从 canonical 开始。CFR 原片可以保留原帧率；VFR 转换默认选 25 fps 并记录源 PTS 到新帧映射。HDR 在没有明确色彩转换与验收前拒绝。不能把归一化后的像素变换声称为原始字节不变。

### 8.2 精确计划

对第 i 个语句，令 `E_i` 为英文结束时间，`S_next` 为下一段任何原始讲话开始时间，`C_i` 为两段之间最后安全整帧边界，`D_i=实际译音采样帧数/sample_rate`，`A_i` 为此前新增定格时长。

```text
E_i <= C_i <= S_next
中文开始 = E_i + A_i + 1
中文结束 = 中文开始 + D_i
新增帧数 H_i = ceil(max(0, E_i + 1 + D_i + 1 - C_i) * fps)
本次新增时长 = H_i / fps
```

源片照常播放到 `C_i`，不足时复制切点之前的最后一帧，暂停源音同样长，再恢复原音画。字幕和中文音频在正常画面到定格的边界连续。选用最后安全切点，避免过早定格；该切点不一定恰好等于下一句采样级起点。不存在安全整帧边界时重新归句或 REVIEW。

尾句将片尾作为下一边界；尾部自然空档先使用，不足再定格。多余原片空档完整保留，不倍速、不缩短原声/译音、不使用 `-shortest` 掩盖差异。

### 8.3 帧与采样换算

不能沿用参考渲染器“每帧整数采样数”的限制处理全部帧率。计划精确保留 `frame/fps`，音频边界用累计绝对时间换算：`round_half_up(t * sample_rate)`；每段样本数取相邻累计边界的差，禁止逐段独立取整累计漂移。原声分段使用同一源边界，保证每个原采样只出现一次；译音保持实际 N 个采样不裁剪。对量化后的 pre/post 再验至少 1 秒，必要时按整帧补足并记录。

25 fps、48 kHz 样例可逐样本严格一致；非整数边界按独立量化预期验证，偏差不超过定义的一采样量化界限。原音在定格处补零；正常自然空档中的源背景声保留，可与译音混合。混音不得偷偷降低原声或时间拉伸；削波转检查失败，需要有记录的混音参数调整后重试。

无损视频逐帧 hash、PCM 逐采样用于验证映射；有损 MP4 检查解码、流结构、持续时间和感知质量，不比较压缩字节相同。生产合成采用顺序流式处理或有界分段，长片不整条装入内存。

## 9. QA、复核与发布

每项检查为 PASS/REVIEW/FAIL/SKIPPED，附 required 与证据。任一必选 FAIL 则 overall=FAIL；必选 REVIEW/SKIPPED 则 overall=REVIEW；全部必选 PASS 且所需人工决定有效才允许发布。阶段一可出媒体演示，阶段二可出候选成片，都不能借未实现检查冒充正式发布。

| 检查 | 必须提供的证据 | 阶段 |
|---|---|---|
| 来源与契约 | source/输入/输出 hash，固定 bindings，schema 和父级关系 | 全部 |
| 时间与完整性 | 独立帧/采样预期，安全切点，至少 1 秒间隔，片尾完整 | 全部 |
| 文本与译音 | ID 对照、数字/否定/姓名等语义复核、真实语音内容与截尾检查 | 二起 |
| 字幕 | 渲染截图、英上中下、缺字/越界/遮挡/切句检查 | 二起 |
| 音色与听感 | 固定参考音及成片抽听区间，按 speaker 的人工结论 | 二起 |
| 去字与画面 | 原图/掩膜/清理帧对照、残字、目标区域外差异 | 三 |
| 成片与发布 | 完整解码、关键段和首中尾复核、发布清单摘要 | 三 |

人工结果只对被审 artifact 有效；上游重做产生新成片后须重新检查对应内容。发布只生成本地不可变 Release 记录和产物清单；上传外部平台不在本计划范围。

## 10. 日志、备份与运维

统一事件字段：event_id、asset、seq、run/execution、node/scope/version、strategy/code、params_hash、input/output_refs、UTC 时间、duration_ms、state/error。stdout/stderr、完整模型响应放版本目录，总日志记录相对路径和摘要。

专项事件：翻译 batch_id/unit_ids/源文计数；TTS speaker/reference/实际块数和采样数；时间线 safe_cut/自然空档/新增帧依据；保存中断位置和恢复动作。JSONL 是数据库事件的可重建视图，按 event_id/seq 去重，不假定两者跨系统原子写入。

备份取得同一运行锁并暂停所有写入，保存数据库完整导出、媒体文件、配置/部署身份和备份校验清单。第一期提供最小备份/还原验证，第三期补齐完整流程资产和发布记录。恢复到新的测试数据根和隔离数据库，核对全部文件哈希、表行数、输入引用和可查询历史后才评估可用；不覆盖活跃生产数据来试验还原。

MySQL 资源报告分别记录容器内存、Docker Desktop 虚拟机额外开销、主控与 worker 进程峰值，以及素材时长/分辨率。768 MiB 初始化失败时保留日志并测量所需调整，更新配置和报告，不宣称预算已经满足。

## 11. 测试编号与验收协议

下表保留三期测试规格。第一期基础项已有实际执行证据并验收 PASS，具体范围见 [最终验收报告](verification/PHASE_1_ACCEPTANCE_ZH.md)；第二期已有候选链路执行证据但扩展验收尚未完成，第三期仍待实现，不能由第一期通过推导其完成。每轮 `evidence/<verification_id>/` 至少包含 environment.json、commands.jsonl、JUnit、case-results.json、输入素材 manifest 和关键产物引用；原始执行证据仍归属 data/videos。case-results 逐项记录环境、素材、步骤、独立预期、断言、结果和证据路径。阶段入口必须检查所需测试确实收集，缺配置或实际依赖时 FAIL；不能用全 SKIP 取得阶段 PASS。

第一期已实现的验收命令（先完成根 README 中的安装与素材准备，输出目录每次新建）：

```bash
.venv/bin/python scripts/verify_phase.py --phase 1 \
  --fixtures tests/fixtures/manifest.json --out evidence/phase1_run_NEW
```

当前脚本支持 phase 1/2；phase 2 保留必需项未通过时的 INCOMPLETE 状态，phase 3 入口仍待实现。各期使用独立输出目录。详见三期 kickoff 的环境、输入和执行步骤。

| 测试 ID | 素材与动作 | 独立预期与断言 | 阶段 / 证据子目录 |
|---|---|---|---|
| T-DB | 真实 MySQL 初始化/迁移、重复迁移、断连重连、重启 | 实际配置与版本吻合、唯一约束生效、数据保留、已提交调用不重复 | 一 / db |
| T-LOCK | 两个本机写入命令，首个正常退出/被杀 | 第二个拒绝；worker 已回收后新命令能获取锁；只读可查询 | 一 / lock |
| T-STORE | 同 bytes 不同文件名、不同 bytes、外部原文件变更 | 同源合并资产但不合并获取/执行，快照独立 | 一 / store |
| T-LINEAGE | 选 A 后运行 B、跨资产/篡改输入、半登记产物 | 绑定不漂移；不合法输入全部拒绝 | 一 / lineage |
| T-RETRY | 成功/失败执行连续重试三次 | 新版本、新目录、三次真实调用；相同输出 hash 允许 | 一 / retry；二加入真实模型 |
| T-CONTRACT | 两种 fixture、真实子进程、越界路径、坏 schema | 标准输出可替换；错误失败且原始日志保留 | 一 / contract；二加入真实 worker |
| T-RECOVER | 在 §4.4 每个登记窗口注入中断 | 只补索引或标中断；输入不可读直到 SUCCEEDED；不再次推理 | 一 / recover |
| T-LOG | 半行、缺行、重建两次 | event_id/seq 唯一、顺序一致、无重复完整事件 | 一 / log |
| T-DOWNLOAD | 同一授权 URL 完整下载两次，另测失败 | 两次实际进程/获取记录；按最终 bytes 决定是否同 asset | 一 / download |
| T-MEDIA | 已标记帧/音频的 CFR、30000/1001、VFR、偏移、旋转；HDR 拒绝样本 | canonical 映射及同步正确；不支持格式明确失败 | 一 / media |
| T-TIMELINE | 短/长/零空档、尾句、连续/交叠、无安全切点、随机有理时间 | 源序列完整、缺口最小、译音不截断、pre/post 达标 | 一 / timeline |
| T-RENDER | 已知逐帧图样与 PCM、不同帧率、片尾 | 母版与独立预期逐帧/采样一致，预览完整解码 | 一 / render；二加入字幕 |
| T-OCR | 人工标注软/硬字幕、短字幕、同画面非字幕文字 | 字文/时差/框误差分别统计；无字框不造值 | 二 / ocr；三做真实 A/B |
| T-SPEECH | 真实录音、短词、无字幕讲话、音乐和多人重叠 | 对齐误差可定位；全部原始讲话保护、风险转复核 | 二 / speech |
| T-UTTERANCE | 多字幕一句、一字幕多句、临界帧边界 | 多对多来源可追溯；错误切点拒绝，重分组新版本 | 二 / utterance |
| T-TRANSLATE | 8/9 段、6000/6001 字符、单句超限、错/漏/重复 ID、真实 Codex | 批次规则确定、完整 ID 守恒、真实回执；人工语义结论 | 二 / translate |
| T-TTS | 单/多 speaker 参考音、英文参考中文目标、生成器多块、真实重试 | 全块拼接、实测采样数、实际模型/设备、音色复核 | 二 / tts |
| T-AUDIO | 空输出、削波、截尾、长短译音、源率转换 | 无声/坏音拒绝；采样量化正确；不以时间拉伸修复 | 二 / audio |
| T-LAYOUT | 多行英中、长句、数字、中文缺字、跨定格边界 | 正确 ID 配对、英上中下、截图无越界遮挡 | 二 / layout；三加入样式匹配 |
| T-STYLE | 已知字体及库外字体、描边、中文 fallback | top-k 与重渲染差异留证；未知不标 exact | 三 / style |
| T-CLEAN | 干净背景烧字，同时放标题/路牌和镜头切换 | 目标字幕清除、允许区外无误改、帧/PTS 保持 | 三 / clean |
| T-QA | 注入错译、缺字、意外黑帧/静音/冻结、缺少必选检查 | 全部阻止错误 PASS；合法定格和源静音按计划排除 | 二 / qa；三扩展完整检查 |
| T-FAULT | worker 超时、kill、磁盘满、半写入、产物篡改 | 证据保留、旧结果不覆盖、恢复/重试行为正确 | 一基础、三完整 / fault |
| T-BACKUP | 暂停任务全备、隔离还原 | 表与全文件摘要/引用一致，历史和发布可读 | 一基础、三完整 / backup |
| T-LONG | 至少 30 分钟、含短长空档的固定素材 | 首中尾映射无累计漂移，输出完整，峰值资源有报告 | 三 / long |
| T-RELEASE | 完整 PASS、缺复核、变更后的成片、失败结果 | 只接受同一固定产物集合；发布清单可独立验哈希 | 三 / release |

模型文字/音色/修复误差阈值由带人工标注的素材评估确定，记录指标定义和阈值版本；首轮不能凭空写“准确率已达标”。媒体整数不变量和 ID 守恒立即作为硬断言。翻译与画面人工验收须逐问题留痕，不能仅写“看起来可以”。

## 12. 分期交付与文档完成标准

| 阶段 | 交付范围 | 独立完成标准 | 执行说明 |
|---|---|---|---|
| 一 | MySQL、运行锁、版本血缘、日志、基本恢复、下载/导入、规范化、时间线和媒体验证 | 真实库稳定、资源实测；重试/恢复正确；无损音画通过；最小备份可还原 | [第一期 kickoff](kickoffs/PHASE_1_KICKOFF_ZH.md) |
| 二 | 字幕/语音证据、归句、Codex、CosyVoice3、音频检查、基准字幕与合成 | 无硬字幕素材生成真实英中交替候选成片，逐句血缘与模型证据完整 | [第二期 kickoff](kickoffs/PHASE_2_KICKOFF_ZH.md) |
| 三 | 硬字幕清理、字体样式、真实 OCR 对照、完整 QA、长片、备份和发布 | 硬字幕素材全链路完成，自动+人工证据齐全，发布可追溯 | [第三期 kickoff](kickoffs/PHASE_3_KICKOFF_ZH.md) |

开始下一期前固定上一期验收报告和代码身份；未完成项按测试 ID 保留，不能沿用历史 SQLite 骨架测试充当 MySQL 验收。当前不开发多主控竞争、数据库版本锁、分布式事务或多语言并行调度。

文档验收：REQ-01–06、N01–20、全部测试 ID 和参考 ID 能在计划与 kickoff 中对应；默认值一致；本地链接有效；待开发命令有明确标识；历史数据保留且不混入新验收。参考文件、函数和核查身份见 [实现参考](IMPLEMENTATION_REFERENCES_ZH.md)。
