# 第一期最终验收

2026-09-12。**第一期 PASS：34 个测试全部通过，无 SKIP；14 个验收 ID 全部 PASS。** 已补齐授权 YouTube 的两次完整下载和独立空数据卷初始化测量。媒体演示的业务 QA 仍为 REVIEW；真实翻译、克隆配音、字幕和业务复核属于第二、三期。

## 下载结果

用户提供的 [YouTube 视频](https://www.youtube.com/watch?v=Ye33eY4UNtY) 为 903 秒、1080p。本轮每次完整下载并合流后的文件均为 69,543,774 bytes，两次独立启动 yt-dlp，随后分别完整解码探测和入库。

| 次数 | 获取 ID | 下载 PID | 结果 |
|---|---|---|---|
| 1 | `acq_655af66cd2ba46acabf2fea6e4683525` | 69384 | SUCCEEDED |
| 2 | `acq_5ba524ff9a744c30bcceec5f8c3a1999` | 69428 | SUCCEEDED |

两份容器文件的 SHA-512 不同，分别按实际 bytes 建立资产；两份文件的音视频编码包哈希完全一致。不能仅凭相同 URL 合并资产，也不能将合流容器元数据差异误判为视频内容不同。完整摘要、时间和媒体参数见 [下载核对](phase1_run_007/download-verification.json)，原始下载日志（stdout 以 gzip 保留全部回车和进度字符）及下载/探测各自的 runtime 均已保存。

## 本轮修复

- 本机 Python.org Python 的默认 CA 文件不存在：本地使用 `/etc/ssl/cert.pem`，保持证书校验；首次 setup 在适用环境下记录该路径。
- 补齐匹配的 `yt-dlp-ejs==0.8.0`，锁定 `yt-dlp==2026.8.19`，显式使用 Node；doctor 增加 Node 检查。依据 [yt-dlp 官方 EJS 指南](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。本机 Node 为 v25.9.0。
- 探测使用独立子目录，保留下载 stdout/stderr 和进程身份；获取恢复同时检查下载与探测进程。真实 HTTP 测试新增日志保留断言。
- 下载失败时，验收结果说明指向实际失败日志，不再误报为缺少用户授权信息。

## 完整回归与代码身份

在独立数据库 `mlvideo_acceptance_20260912_007` 和数据根 `evidence/phase1_data_007` 中执行，原业务库/数据根保留：

```bash
MLVIDEO_YOUTUBE_URL='https://www.youtube.com/watch?v=Ye33eY4UNtY' \
MLVIDEO_YOUTUBE_AUTHORIZATION='User supplied this URL for the requested download acceptance test on 2026-09-12.' \
.venv/bin/python scripts/verify_phase.py --phase 1 \
  --config config/local-acceptance-007.json \
  --fixtures tests/fixtures/manifest.json --out evidence/phase1_run_007
```

退出码 0；pytest 为 **34 passed in 25.00s**。另通过 Ruff F 检查及 `pip check`。机器可读证据：[阶段结果](phase1_run_007/case-results.json)、[JUnit](phase1_run_007/junit.xml)、[环境](phase1_run_007/environment.json)、[代码快照身份](phase1_run_007/code.json)、[证据摘要索引](phase1_run_007/evidence-index.json)。代码快照包含 42 项 Git 源码/配置/测试文件和 5 项 editable 安装元数据；提交前逐项核对实际文件摘要一致。

本地配置不提交凭据；复现时按 `config/pipeline.json` 建立独立数据库/数据根配置并授权。原始代码 ZIP、命令记录、媒体演示和完整执行历史保留在本地 evidence 目录。

## 空数据卷初始化

`scripts/measure_cold_start.py --out evidence/mysql_cold_001` 建立独立的新 named volume 和临时容器，使用已锁定的 MySQL 镜像、同一 mysql.cnf/初始化 SQL、768 MiB 限制；不映射网络端口。测试结束后仅清理该临时容器和卷。

- 初始化到最终服务器就绪：约 **5.11 秒**。
- 40 个 cgroup 内存采样中的最高值：**239,427,584 bytes（约 228.3 MiB）**，包含页缓存。
- 采集进程 RSS 峰值：约 21.4 MiB；`oom=0`、`oom_kill=0`、`OOMKilled=false`。
- 实际 MySQL 8.4.11、buffer pool 134,217,728 bytes、max_connections=10、Performance Schema=0、binlog=0。

[结果](mysql_cold_001/result.json)、[采样](mysql_cold_001/memory-samples.jsonl)、[初始化日志](mysql_cold_001/mysql.log) 已保存。内核不提供 memory.peak，因此明确报告离散采样最大值，不声称获得精确瞬时峰值。已有进程重启、空闲、连续任务和 Docker Desktop 额外开销记录见本轮 [resources.json](phase1_run_007/resources.json)。

## 历史证据

先前失败获取记录均保留。`phase1_run_005` 记录证书失败；`youtube_retry_001` 记录 JS 组件诊断；`phase1_run_006` 已下载成功，但下载日志被探测覆盖，最终以修复后的 `phase1_run_007` 为准。此前第一期未完成报告保留为历史。

为释放本次自测所需空间，将 `evidence/development` 的 9,964 个开发自测文件归档为 `evidence/development-preserved.tar.gz`；逐文件比较 SHA-512 全部一致后才移除原目录。正式证据和已导入视频保留；[归档记录](phase1_run_007/development-archive.json) 可查。
