# 第一期实现与自测报告

> 2026-09-12 更新：第一期已 PASS，下载和空卷初始化缺口已补齐，详见 [最终验收](PHASE_1_ACCEPTANCE_ZH.md)。以下保留 2026-09-11 的历史记录。

2026-09-11。最终证据轮次：`evidence/phase1_run_004`。**34 个测试全部通过，无 SKIP；13 个基础验收 ID 为 PASS。阶段整体 INCOMPLETE：缺少同一授权 YouTube URL 的两次真实下载。**

## 已交付

- `src/mlvideo`、全局 JSON 配置、doctor、CLI、锁和独立 worker 协议；环境缺失/错误返回非零。
- MySQL 8.4.11（实际拉取的 digest 已锁定）、768 MiB 容器预算、128 MiB buffer pool、10 连接、禁用 Performance Schema/X Plugin/binlog、独立应用和迁移账号、PyMySQL 自动提交、编号迁移及校验和。
- 源 SHA-512 归档、每次独立 acquisition、完整解码探测；下载失败保留 incoming。源文件变更不影响已归档快照。
- execution/version、固定端口 ArtifactRef、保守文件父级、源码 ZIP 与摘要、请求和封存清单、DB 事件与可重建 JSONL。
- 顺序配方、历史选择、新版本真实 retry、原 execution recover；启动闸门先记录 worker 身份，再执行。主控被杀后阻止新业务，恢复先处理旧进程组。
- N04 CFR/48 kHz PCM16 双声道及逐帧 PTS 映射；VFR、旋转、正负音轨偏移。音频越过视频首尾时补画面，保留音频采样；不能解释的音频 PTS 间断和 HDR 明确拒绝。
- N16 空档优先有理数时间线、整帧定格、累计采样量化；N18 管道逐帧渲染 FFV1/PCM 母版与 H.264/AAC 预览。提示音是阶段一隔离算法素材。
- 同一写入锁下完整逻辑数据库 JSON 导出、文件摘要清单、独立库/空目录还原与还原后真实重试。

## 实际验证

最终命令：

```bash
.venv/bin/ruff check src scripts tests --select F
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python scripts/verify_phase.py --phase 1 \
  --fixtures tests/fixtures/manifest.json --out evidence/phase1_run_004
```

pytest 返回 0；阶段入口返回 1，正确保留下载未完成状态。机器可读结果见 [case-results.json](phase1_run_004/case-results.json)，完整测试收集见 [JUnit](phase1_run_004/junit.xml)。

| 验证 | 实际行为与独立断言 |
|---|---|
| 数据库/存储/血缘 | 真实 MySQL 迁移、唯一约束、账号权限、重启保留、真实 KILL CONNECTION；同 bytes 不同名字合并资产但保留新获取记录；跨资产/篡改/未成功上游拒绝 |
| 重试/协议 | fixture A/B 真实子进程、成功后连续三次新调用；完整 N03→N04→N16→N18 配方及真实渲染重试；相同解码帧且新 execution/版本 |
| 恢复/日志 | request 未登记、输入已登记、清单封存、两产物中仅一项登记、结束事件已落库、成功后回执丢失六窗口；不重复执行 worker；JSONL 半行重建 |
| 生命周期/故障 | 第二写入拒绝且只读可用；主控 SIGKILL、worker 超时；16 MiB HFS+ 镜像内真实 ENOSPC，FAILED 且无开放产物 |
| 媒体/时间线 | 25 fps、30000/1001、VFR、旋转、正负偏移、HDR 拒绝；短/长/零空档、尾部补帧、多切点、非整数长尾；逐帧像素与逐 PCM 采样独立比较 |
| 下载器 | 对本机 HTTP 服务上的原创 MP4 两次真实 yt-dlp 下载；独立 acquisition、相同源 SHA；真实 HTTP 404 失败留证。**不计作 YouTube 下载验收** |
| 备份 | 真实库全表导出、全文件摘要、隔离库还原、还原源 artifact 可读、还原后 retry 成功 |

关键结果及本地原始证据的摘要见 [evidence-index.json](phase1_run_004/evidence-index.json)。完整命令输出、源码快照、恢复记录、母版和预览在本地 `evidence/phase1_run_004/`；Git 保存精简报告和原始 JUnit，避免将重复的测试媒体/备份写入仓库。此前 development/001–003 为开发过程，不作为最终代码的验收。

## 资源与运行身份

| 采样点 | MySQL 容器 | 验收主控 RSS | Docker Desktop 相关进程 RSS 合计 |
|---|---|---|---|
| cold_process_restart_persistent_volume | 187.7MiB / 768MiB | 39.6 MiB | 8.50 GiB |
| idle_after_restart | 188.8MiB / 768MiB | 40.2 MiB | 8.42 GiB |
| after_continuous_tasks | 202.2MiB / 768MiB | 50.6 MiB | 8.42 GiB |

资源详表见 [resources.json](phase1_run_004/resources.json) 和 [连续任务容器采样](phase1_run_004/container-samples.jsonl)。冷启动采样指保留数据卷的 MySQL 进程重启，不是空卷初始化峰值；RSS 合计包含其他容器及共享映射，不能归因给本项目。所有采样容器 OOMKilled=false，768 MiB 预算下运行成功。测试进程逐 PID 的采样峰值也已记录，不将离散采样宣称为精确连续峰值。

执行时尚未提交新文件，故代码身份如实记录 base commit 和 dirty=true；[code.json](phase1_run_004/code.json) 保存全部 46 项实际部署文件（包含 editable 安装元数据）及 SHA-512，并附本地源码 ZIP 的摘要。本报告生成时逐项核对当前文件与该快照一致。提交后另核对了 41 项 Git 文件与实现提交 `ce56eaf` 完全一致；5 项生成的 egg-info 元数据保留在快照中、不进入 Git，详见 [提交核对](phase1_run_004/commit-verification.json)。源码树 SHA-512：

`b2f28143645f866e20c234d422de96d2d9923bf2fc3486a9b7c65518aee697ef18f5da881dec1ba3ce8b46ee20ce9faab2c7dc667fbb1b91e9af00c5545aabee`

依赖见根 `requirements.lock`，实际镜像/FFmpeg 身份见 `config/upstreams.lock.json`。MySQL 初始化曾受 Docker Desktop 挂载 shell 的执行权限影响；最终使用不进入 Git 的本地初始化 SQL。

## 未完成项与交接

1. **T-DOWNLOAD 未完成**：需提供授权 YouTube URL 和授权说明，设置 `MLVIDEO_YOUTUBE_URL`、`MLVIDEO_YOUTUBE_AUTHORIZATION` 后重新执行新轮次验收。未用本地 HTTP 下载冒充外部 YouTube 成功。
2. 资源采样没有空数据卷初始化期间的连续峰值；现有记录是成功初始化后的进程重启、空闲和连续任务测量。
3. 整体业务 QA 始终 REVIEW。真实翻译、CosyVoice3、字幕、音色与人工复核属于后续阶段，均未宣称通过。
4. 当前只支持本机 worker；不能确认旧进程组终止时停止恢复。恢复目录/数据库必须隔离；代码版本复现需部署快照对应代码。媒体检测存在时间戳不连续或多原声轨时拒绝处理，需明确处理策略。

项目入口与配方使用见 [根 README](../../README.md)。后续继续 [第二期 kickoff](../kickoffs/PHASE_2_KICKOFF_ZH.md) 前，保留本期未完成项。

实现参考：基础文件工具改编自本仓库 VidFlow 参考包的 util；执行器、MySQL、恢复、量化及流式渲染按当前规格新写。配置与工具接口核对了 [MySQL X Plugin 文档](https://dev.mysql.com/doc/refman/8.4/en/x-plugin-disabling.html)、[PyMySQL Connection](https://pymysql.readthedocs.io/en/latest/modules/connections.html) 和 [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html)。
