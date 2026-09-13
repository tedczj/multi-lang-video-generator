# temp-0914 当前分支补丁审查（2026-09-14）

## 结论与基线

已在 `main` / `b7063f99bacdd7ecaa8692a774a0c99c4e5043e0` 应用交付补丁，并修复下述两项业务门禁漏洞及浏览器测试兼容问题。主要人工闭环设计与当前实现相符；本报告不构成真实模型、音色或整片人工验收。

开始时工作区仅有未跟踪的 `temp-0914/`。原始 PATCH 的 `git apply --check` 通过；交付 ZIP 与 HEAD 的同名已跟踪文件逐字节比较，只有补丁声明修改的 8 个既有文件不同，其他共同文件一致。001 migration 未修改。未覆盖本机私有配置，未升级业务数据库，未提交或推送 Git。

`verification/` 是交付方历史证据，保留原样；本次证据位于 `evidence/studio_review_0914/`（沿用仓库 ignore 规则，本机保留）。

## 发现并修复

1. **P1：更换原声绑定仍继承人工标注。** `Catalog.create_revision` 原来仅比较片段 ID、时间、文字，重新接入另一版规范化音频/分析仍可携带原角色确认。现在只有六个原声/分析绑定均相同才允许携带；改变绑定后重新审核。`test_revision_does_not_carry_reviews_to_replaced_audio` 在修复前失败，修复后通过，另在真实 MySQL 复验。
2. **P1：参考区间内遗漏讲话未阻断。** `Catalog.create_reference` 原来只检查工作台现存分段，删除或遗漏中间讲话后可跨两段同角色台词截取含未知讲话的参考。现在将范围内 ASR 与 VAD 讲话逐段对照已确认片段的覆盖并阻断空洞；真实静音间隔仍可保留。`test_reference_rejects_unreviewed_speech_in_gap` 修复前失败，修复后通过，另在真实 MySQL 复验。
3. **P2：原生 HTTP 浏览器验收脚本不可直接在本机执行。** 原来硬编码 Linux Chromium 路径，字符串谓词在应用 CSP 下触发 unsafe-eval 拒绝。改为 `MLVIDEO_TEST_BROWSER` 可指定浏览器（默认 Playwright 浏览器），等待使用函数谓词；未改变应用 CSP。真实 Chrome / HTTP 的六项交互已通过。
4. 去掉新工作台模块中 3 个未使用导入。补充 worker 重启不重跑 RUNNING 任务、恢复无 studio 表的旧备份两项回归。

## 设计符合性

| 设计要求 | 本次审查与证据 | 结论 |
|---|---|---|
| MySQL 增量迁移、保留原 CLI/001 | 002 只添加 studio 表；真实 8.4.11 migration、重复 migrate、旧 CLI 回归 | 符合已测范围 |
| 系列/角色稳定 ID 与跨系列隔离 | 外键、character_in_series、跨系列导入拒绝 | 符合 |
| 不可变 revision、追加标注、乐观锁 | 分段修改、批量回滚、冲突、源绑定携带回归 | 修复后符合 |
| 建议独立、不自动确认角色/译文 | suggestions 独立表、前端显式接受、原标注不覆盖 | 符合；非跨集声纹识别 |
| 连续 1–30 秒参考、角色纯净、独立批准 | 已知 ASR/VAD 覆盖门禁、四项人工批准、停用阻断 | 修复后符合；听感仍需人审 |
| 固定 reference/model/seed，测试后发布 | Profile SHA-512、同 profile 成功试听 job 绑定、FAIL 阻断 | 符合；模型调用用显式替身验证 |
| Plan 固定分段/标注/译文/声音及模型 | SHA-512 校验、固定 N08/N09 产物、旧计划不读取最新文本 | 符合 |
| 重试新执行，明确选用及 render 快照 | 跨集真实媒体链路、调用次数、选用后入队再换音频测试 | 符合 |
| 跨集只开放两个 N10 导入端口 | 同系列与固定 profile 校验，真实源 asset 父边；普通 artifact 拒绝跨集 | 符合 |
| 单媒体写入、短目录事务、重启不自动复跑 | 独立 worker/连接、锁序代码审查、旧 CLI 锁与故障恢复、worker 重启回归 | 符合已测范围；未做长时并发压测 |
| 本机安全、已登记媒体和范围播放 | Host/Origin/token/字段校验、WAV Range；Chrome 原生 HTTP | 符合已测范围；非完整安全审计 |
| 备份含新表、兼容旧备份 | MySQL 全部表记录数及文件哈希恢复；协议层旧备份恢复；旧 CLI restore/retry | 符合已测范围 |
| N16–N19 复用与候选 REVIEW | 真实 FFmpeg MP4、时间线/QA、跨集父边检查 | 符合；未验证真实整片质量 |

## 本次验证

- `pytest tests/unit tests/integration/test_phase2_media.py tests/studio`：**109 passed、0 skipped**，结果见 [pytest.log](../evidence/studio_review_0914/pytest.log) 和 [pytest.xml](../evidence/studio_review_0914/pytest.xml)。含修复回归与重启/旧备份补充检查。
- 真实 MySQL 8.4.11 跨集生成、重试、选用、合成：[mysql.json](../evidence/studio_review_0914/mysql.json)。TTS 是明确提示音替身，ASR 是声明的夹具。
- 两项新业务门禁在真实 MySQL 复验：[mysql-final.log](../evidence/studio_review_0914/mysql-final.log)。
- MySQL 工作台备份/恢复（全部核心与 studio 表行数、全部恢复文件 SHA-512）：[mysql-backup.json](../evidence/studio_review_0914/mysql-backup.json)。
- 旧 CLI MySQL 回归：**9 passed、17 deselected**，覆盖 retry lineage、六个恢复故障点、锁/超时/终止、备份恢复重试和数据库重启：[legacy-final.log](../evidence/studio_review_0914/legacy-final.log)。没有把其余 17 项称为已通过。
- 真实 Chrome → 127.0.0.1 HTTP → FastAPI：**6 checks passed、0 console errors**，涵盖建角色、标注翻译、WAV 解码时长、筛选、拆段、角色声音页：[browser-smoke.json](../evidence/studio_review_0914/browser/browser-smoke.json)。此层数据库为测试协议适配器，不能称为浏览器 + MySQL + 真模型全栈验收。
- `git diff --check`、Python 编译、JS 语法与 Ruff 关键错误集合 `E9,F63,F7,F82,F401` 通过。**默认全规则 Ruff 未通过：51 项**，包括 Depends 默认参数告警、导入排序和原模块已有规则告警；没有为消除告警而批量重构旧代码。[完整输出](../evidence/studio_review_0914/ruff-full.log)。
- pytest 有一条 Starlette/AnyIO 弃用警告。

曾出现并保留的失败：初始缺 FastAPI，安装工作台依赖后通过；浏览器 CSP 等待脚本失败后修复；旧 CLI 首轮使用相对 evidence 路径，restore 后 retry 路径比较失败，改用绝对路径及正式受限测试账号后 9 项通过（未修改旧引擎路径规则）。修复前的两条业务回归失败日志也保留。

## 待验收与复现

真实 YouTube/Whisper/Codex/CosyVoice3 下载到成片流程、真实中文听感和音色一致性、业务库原地升级/恢复演练未在本次执行。没有替用户批准任何真实参考或声音。人工确认与整片 REVIEW 门禁保持。

本机测试库为 `mlvideo_0914_studio_acceptance`、`mlvideo_0914_restore_studio_acceptance` 及旧 CLI 自动创建的独立 restore 库；测试文件在 `/tmp/mlvideo_0914*`、`/tmp/mlvideo-studio-ui-0914` 和 evidence 下。保留以便审计，未清理共享存储。

```bash
PYTHONPATH=src MLVIDEO_TEST_LATIN_FONT='/System/Library/Fonts/Supplemental/Arial.ttf' \
  .venv/bin/python -m pytest tests/unit tests/integration/test_phase2_media.py tests/studio -q
# 先在一个新的隔离目录启动测试服务，再在另一终端运行浏览器验收：
.venv/bin/python tests/studio/browser_seed_server.py /tmp/new-studio-ui-fixture
MLVIDEO_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  .venv/bin/python tests/studio/browser_smoke_http.py /tmp/new-studio-ui-fixture /tmp/new-studio-ui-results
```

## 后续用户调整：启动正式工作台、取消姓名（2026-09-14）

按用户明确要求移除审核人姓名输入与浏览器必填校验。API 未提交 reviewer 时使用非实名标记 `local-user`，保留已有记录格式、审核说明、勾选项与版本血缘；不把此标记视为已验证的人类身份。此要求取代原设计的具名审核要求，未改变真实试听/确认门禁。

已用 HEAD 的升级前备份实现备份业务库及 4455 个文件（APFS 写时复制，保留文件摘要），随后执行 migration 002，启动 `http://127.0.0.1:8787` 及独立 worker，配置为 `config/local-phase2-001.json`。备份、迁移结果、日志和正式页面浏览器证据位于 `evidence/studio_start_0914/`。API 回归 7 项通过，覆盖不填写姓名保存标注与创建参考；本次没有代替用户进行真实内容批准。正式目录初始为空，已有底层素材仍可接入。

## 再次磁盘耗尽后的保护修复（2026-09-14）

真实重试在 N04 生成约 1.85 GiB 规范化视频后，补帧需要额外临时副本，磁盘再次耗尽。Docker 虚拟文件系统报 read-only，容器健康状态不能证明宿主 3307 可连接。按先前用户授权清理该失败执行的两个未登记 work 视频（约 2.73 GiB），保留原片、请求、日志、哈希审计；确认进程组已结束且无 manifest/artifacts，数据库恢复后再核对 execution FAILED、零登记产物。

增加 worker 启动前和运行中每秒磁盘检查，保留 1 GiB 系统余量；不足时终止整个 worker 进程组并记录失败，不自动重试。N04 补帧前按现有视频的 1.2 倍临时容量加 1 GiB 余量检查峰值需求。空间判断不保证整片全流程可完成，仍需为实际媒体准备足够容量。工作台重连增加 InterfaceError，修复此次异常令 worker 退出的问题。

验证、生产页面及自动轮询证据见 `evidence/studio_disk_guard_0914/`；磁盘清理审计见 `evidence/studio_refresh_0914/repeated-disk-cleanup.json`。真实单集页面、任务与系列 API 恢复 200，浏览器无脚本错误，未重跑已中断任务。

本轮最终回归：117 passed，1 条依赖弃用警告；保留 XML/日志后清理本会话 pytest 临时媒体目录。

可随 Git 查阅的最终测试日志和摘要见 [verification/studio-review-20260914](../verification/studio-review-20260914/README.md)；本地 evidence 内的其余记录不随 Git 提交。
