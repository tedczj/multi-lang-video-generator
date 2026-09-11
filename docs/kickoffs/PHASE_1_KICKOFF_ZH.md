# 第一期 kickoff：基础设施与媒体执行

状态：待开发。基线：[完整开发计划](../DEVELOPMENT_PLAN_ZH.md)，需求 REQ-01、REQ-03–06 及 REQ-02 的媒体子链。所有项目命令、配置和测试入口须在本期实现后才能运行。

## 1. 目标与前置条件

交付一个使用真实 MySQL 的串行执行内核，证明导入、版本血缘、真实重试、恢复和空档优先音画处理可以实际运行。本期用已知文字/语句时间和提示音隔离媒体算法；不把演示当作真实翻译成品。

前置环境：Mac、Python 3.11+、Docker Desktop、支持 FFV1/libx264/AAC 的 FFmpeg/ffprobe、可访问的授权下载样本。先记录环境版本和可用磁盘；Python 命令必须来自满足版本要求的环境。现有根目录尚无项目安装文件，不能直接执行旧 README 的安装命令。

阅读：[主计划 §3–5](../DEVELOPMENT_PLAN_ZH.md#3-mysql文件与索引)、[时间线 §8](../DEVELOPMENT_PLAN_ZH.md#8-空档优先时间线与音画)、[实现参考](../IMPLEMENTATION_REFERENCES_ZH.md)。优先参考 REF-CORE、REF-DOWNLOAD、REF-MEDIA、REF-TIMELINE。

## 2. 按顺序交付

| 顺序 | 实现内容 | 接口 / 产物 | 验证 ID |
|---|---|---|---|
| 1 | 建 `src/mlvideo`、配置、doctor、测试收集与证据入口 | doctor 输出实际 Python/工具/镜像/数据库和缺失项；缺必需环境非零退出 | T-DB、T-CONTRACT |
| 2 | Compose + MySQL 配置、PyMySQL、编号迁移 | 主计划 §3 全部必要表；autocommit=True；迁移校验和 | T-DB |
| 3 | 全局本机写入锁；worker 进程组生命周期 | 第二个写入立即拒绝；只读命令可用 | T-LOCK |
| 4 | N01–N03：下载/本地导入、SHA-512、探测 | AcquisitionReceipt、SourceAsset、MediaProbe；未知 SHA 留 incoming | T-DOWNLOAD、T-STORE |
| 5 | execution/version、固定端口输入、文件父级、策略注册 | request、manifest、ArtifactRef；标准 worker 协议 | T-LINEAGE、T-CONTRACT |
| 6 | retry/select/recover、顺序配方、DB 事件与 JSONL | 每次有效 retry 真调用；封存清单可恢复；不重跑模型来修索引 | T-RETRY、T-RECOVER、T-LOG |
| 7 | N04：归一化和时钟映射 | CanonicalMedia、CFR/PCM、源到 canonical 映射 | T-MEDIA |
| 8 | N16 + N18 媒体子集 | TimelinePlan、无损母版、预览；源音画同时定格 | T-TIMELINE、T-RENDER |
| 9 | 最小暂停备份与隔离还原、资源报告 | 数据库导出+文件清单；冷/闲/连续任务资源证据 | T-BACKUP、T-FAULT |

数据库参数固定为 MySQL 8.4、127.0.0.1:3307、128 MiB buffer pool、10 连接、关闭 Performance Schema/X Plugin/binary log、768 MiB 初始容器预算。锁定实际补丁镜像及 digest；不要写一个未经拉取验证的 digest。资源报告分别记录容器、Docker Desktop 和主控占用。

## 3. 首版 CLI 契约

这些是本期交付目标，命令统一采用 `python -m mlvideo.cli`：

| 命令 | 输入 | 返回 / 行为 |
|---|---|---|
| `doctor` | 配置和环境 | 工具、代码、真实 DB 状态；不证明模型/网络就绪 |
| `migrate` | 顺序 SQL | 已应用版本和校验和 |
| `ingest PATH` / `download URL` | 本地文件 / 授权页面 | acquisition_id、asset_sha512、source_artifact_id |
| `run ASSET NODE STRATEGY --inputs FILE --params FILE` | 固定 artifact 映射与参数 JSON | execution_id、version、state、artifacts |
| `pipeline ASSET --recipe FILE` | 明确顺序依赖配方 | run_id、固定 bindings、执行集合与终态 |
| `retry ASSET EXECUTION` | 原执行身份 | 新版本、新调用、retry_of |
| `recover ASSET EXECUTION` | 中断执行 | 补齐登记 / INTERRUPTED，保留原 execution |
| `select ASSET ROLE ARTIFACT --reason TEXT` | 明确历史产物 | 新 selection_id |
| `history ASSET` / `status ASSET` | asset | 只读结果，包含失败/中断和质量状态 |
| `backup --out DIR` | 新目录 | 取得运行锁，完整导出+文件与校验清单 |
| `restore BACKUP --config ISOLATED_CONFIG` | 隔离恢复目标 | 校验结果；目标不得是活跃数据根/数据库 |

配置加载与 `--config` 为全局参数，位置在子命令之前。CLI 结构化输出 JSON，错误非零退出。外部 worker 输入输出协议与端口映射使用 [主计划 §5](../DEVELOPMENT_PLAN_ZH.md#5-插件标准结果与代码身份)。

## 4. 素材、验收命令与独立预期

准备 `tests/fixtures/manifest.json`，每项含 case_id、文件路径、SHA-512、授权说明、媒体属性、人工/程序预期文件及其哈希。素材包含：

- 25 fps、48 kHz 双声道的逐帧编号和可逐采样检查 PCM，分别覆盖短/长/零空档与尾部补帧。
- 30000/1001 CFR、VFR、非零音视频偏移和旋转样本；HDR 用于验证未支持路径会明确拒绝。
- 已标注讲话区间与安全切点的模拟输入，以及交叠/无安全整帧边界的拒绝样本。
- 同 bytes 不同名字、被篡改输入，以及同一授权 YouTube URL 的两次真实下载。

未来在项目根目录运行；目录名每次新建，禁止复用旧证据：

```bash
docker compose config --quiet
docker compose up -d mysql
python -m mlvideo.cli doctor
python -m mlvideo.cli migrate
python -m pytest tests/unit -q
python scripts/verify_phase.py --phase 1 \
  --fixtures tests/fixtures/manifest.json --out evidence/phase1_run_001
```

验收入口负责真实 MySQL/媒体/下载/故障测试，不允许以 fixture 或 mock 替代这些外部依赖。开发可单独跑逻辑测试；阶段一 PASS 必须有真实数据库与网络下载结果。网络未具备条件时保留未完成项，不宣布全期通过。

独立期望由人工定义的小样或独立 oracle 生成，不能直接调用被测 planner 返回 expected。无损源帧依序出现一次，只允许计划定格帧；PCM 原声/译音与期望位置一致。非整数帧率遵守累计边界量化，检查首中尾无累积偏移。

故障依次注入：进程超时、主控被杀、部分 artifact 登记、DB 提交回执丢失、JSONL 半行、受限临时盘写满。每个场景记录执行 ID、注入位置和恢复后的 DB/文件对照；不在真实系统盘写满来模拟故障。

## 5. 完成标准与交接

T-DB、T-LOCK、T-STORE、T-LINEAGE、T-RETRY、T-CONTRACT、T-RECOVER、T-LOG、T-DOWNLOAD、T-MEDIA、T-TIMELINE、T-RENDER、T-FAULT 基础项、T-BACKUP 基础项均有新执行证据。没有收集到测试、必需测试 SKIP、数据库不可用都不能通过本期。

交付固定代码身份、依赖/镜像锁、SQL 迁移、配方、素材清单、验收报告、母版/预览及逐帧逐采样结果。媒体演示的整体业务 QA 保持 REVIEW，未实施的 AI/字幕检查逐项注明。

将接口和报告交给 [第二期 kickoff](PHASE_2_KICKOFF_ZH.md)。完整业务完成标准仍以 [主计划 §12](../DEVELOPMENT_PLAN_ZH.md#12-分期交付与文档完成标准) 为准。
