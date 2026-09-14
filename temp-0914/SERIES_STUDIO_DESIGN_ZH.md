# 系列角色配音工作台：基于当前源码的增量设计

## 1. 基线与边界

以用户上传的 `f8fe40c8-1163-4fb1-a54b-3aa5c913b7ed.zip` 为唯一代码基线。当前实现是 `src/mlvideo`、MySQL 8.4、`.writer.lock` 单媒体写入者及独立模型 worker，不是早期 SQLite/bivideo 骨架。保留全部旧 CLI、001 migration 和旧策略语义。

本次完成的目标是人工闭环：系列→视频→原声分段→角色/文本审核→优选参考→角色声音配置试听与发布→固定版本逐句生成→选择译音→沿用 N16/N17/N18 生成候选视频。自动声纹跨集识别不是首版前置条件。系统建议独立存储，不会覆盖确认结果。模型质量仍须真实人声试听，程序测试不构成“像本人”的证明。

## 2. 模块与数据

- `studio/catalog.py`：MySQL 目录、不可变分段版本、追加式标注、参考批准、声音发布、计划快照、选用与事件。
- `studio/jobs.py`：持久队列和单任务执行器。下载/模型不在 HTTP 请求线程执行。失败不自动重试模型；手动重试创建新 job/execution。
- `studio/nodes.py`：新增明确策略，生产既有契约。N08 人工复核、N09 手动译文、N10 人工参考与受控跨集导入。
- `studio/api.py` + `static/`：本机 FastAPI 和无 CDN 的原生前端，系列、角色、参考、分段审核、声音测试、任务及输出页面。
- `migrations/002_series_studio.sql`：只增加 `studio_*` 表，不重写 001。

Series/Character/Episode 用稳定 ID，名字不当外键。Episode 的 active revision 是投影，revision 内的原声 bindings、分段和摘要不可变。Annotation 以 `(revision_id, segment_id, version)` 作用域追加；同名片段不能跨分段版本串接。更新要求 expected_version，旧页面提交返回冲突。变更边界/拆合并产生新 revision；只有内容指纹完全一致的片段可携带有原始来源的标注。

Reference 与角色标注独立：候选→生成参考文件→人工批准→可进入声音配置；停用不删除旧文件。每个 profile 首版固定一个已批准参考（角色库可有很多），保存真实部署配置和 seed 的快照，禁止从前端提交 Python/命令路径。先生成测试音，再由用户具名批准并设为默认。加入新参考不会自动改变 profile。

## 3. 工作流与版本锁定

1. 选择系列提交 YouTube URL，登记 Episode 和 PREPARE job。仅允许 YouTube HTTPS URL，不接受任意下载地址或客户端可执行命令。
2. 复用 ingest/N03/N04/N07，默认可从 ASR 生成明确标注来源的候选字幕；配置为 OCR/soft 时仍调用现有 N05/N06。原始 ASR/VAD 的 REVIEW 不伪造成确认。
3. 将 ASR 分段导入工作台，不先要求聚类正确。页面试听片段/上下文、标角色、改英文与中文；未知/重叠保持阻断。
4. 从已确认角色片段截取 1–30 秒连续参考，可覆盖同一角色多个相邻台词。检查范围内的已知讲话均属于该角色，准确转录另由用户确认。不把所有样本默认拼接。
5. 创建 profile，生成中文测试；用户确认听感后发布。现有 CosyVoice3 worker 原样复用。
6. 冻结完整 Plan：revision、每条 annotation、中文文本、角色、profile、参考及模型配置。计划材料化产生 N08/N09 固定产物；后续单句重做都引用这些固定产物，而不重新读 latest。
7. 每次生成保存新 N11/N12 execution。用户选择具体 job 中的具体句子版本。生成新结果不会自动改变选用或历史成片。
8. Render job 入队时固定全部选用记录，再调用原 N16/N17/N18/N19；新译音时长触发新时间线。整片依然是 REVIEW 候选，不自动发布或上传。

## 4. 跨集参考和并发

不修改普通 `Engine.artifact` 的同 asset 规则。仅 `N10/studio_import_reference` 的两个固定输入端口允许跨 asset，且必须匹配目录里已批准 reference/profile、参考的真实原 asset、目标系列成员关系。Engine 的执行、登记和恢复均走同一授权逻辑，产物父边保留真正的跨集源引用。worker 输出目标视频内的新不可变参考副本，N11 继续使用同 asset 输入。

已有 `.writer.lock` 继续串行化所有媒体节点。工作台短目录写入使用 `.studio.catalog.lock` + MySQL transaction，不持锁调用模型；因此推理中可继续审核下一集。整个工作台写操作通过此锁，锁顺序为 media writer → catalog；HTTP 不反向获取 media writer。备份同时取得两锁，包含新表与快照。单个 task runner 另持 `.studio.worker.lock`，使用独立 DB 连接；HTTP 连接不与任务线程共享。

审核暂停是数据库持久状态，没有等待点击而挂起的 worker。服务重启不自动复跑 RUNNING 模型任务；明确标 INTERRUPTED，必要时先用原 CLI recover 恢复执行，再创建重试。

## 5. 安全、存储与恢复

默认只绑定 127.0.0.1，禁止 --host 0.0.0.0。Host 白名单、同源 Origin/Fetch Metadata 和每会话写入 token 防止其他网页触发本机操作。不提供任意路径下载、任意 subprocess 或请求方指定模型环境。媒体读取只接受已登记 artifact ID，核验状态、路径边界、大小和摘要；参考试听由样本范围流式读取，不复制整个原 WAV。

目录元数据事务保存在 MySQL，事件追加记录审计。Plan/Profile JSON 的 SHA-512 随记录保存，执行重新校验。媒体字节仍在 videos/<sha512>，不复制一套 series 视频目录。禁用参考阻止新的使用，但历史文件和成片保留。停用/换角色不会偷偷改已冻结计划，页面提示旧计划需重建。

## 6. 测试分层与不作假 PASS

- 原项目 unit + 无模型 FFmpeg integration 回归。
- 新增领域规则、跨系列拒绝、乐观锁、标注版本隔离、参考批准/停用、声音发布门禁、计划固定、重试、选用快照、备份兼容、安全接口测试。
- SQLite **仅作为测试中的 SQL 协议适配器**，不增加生产数据库选项，不将其结果称为 MySQL 8.4 验证。
- 模型边界可用显式 test double 验证真实 Engine/worker/文件/媒体链路；不把测试提示音标注成中文语音或真实克隆。
- 提供真实 MySQL 和现有模型配置下的本机验收命令；本环境未执行的项目明确列出。
