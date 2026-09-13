# multi-lang-video-generator

Mac 主控、MySQL 8.4、单写入者和独立 worker 的版本化媒体流水线。第一期实现媒体执行，不包含真实翻译、克隆语音或字幕制作；提示音演示的业务 QA 始终为 REVIEW。

第一期已于 2026-09-12 验收 PASS：34 个测试通过、14 个验收 ID 全部通过，详见 [最终验收报告](docs/verification/PHASE_1_ACCEPTANCE_ZH.md)。第二期已接入真实翻译、ASR/VAD、CosyVoice3 与双语候选成片链路，见 [整体验证与问题记录](docs/verification/PHASE_2_FULL_VERIFICATION_ZH.md)。正式视频集和人工质量验收仍未完成；第三期待开发。


## 系列角色配音工作台（本次增量）

新增本机管理页：系列/角色库、原声分段试听、手工与批量标注、参考音频批准、固定声音配置、中文测试与发布、版本化逐句配音、音频选用和候选成片。继续使用当前 MySQL 与 CosyVoice3，不引入第二套生产数据库，不需要 npm 或前端 CDN。

- [升级与完整使用步骤](docs/STUDIO_QUICKSTART_ZH.md)
- [基于当前源码的设计](docs/SERIES_STUDIO_DESIGN_ZH.md)
- [实现与测试边界](verification/STUDIO_IMPLEMENTATION_REPORT_ZH.md)

```bash
# 先按升级文档使用旧代码备份；保留你原来的私有配置和数据目录。
.venv/bin/python -m pip install -r requirements.studio.txt
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json migrate
.venv/bin/python -m mlvideo.cli --config config/local-phase2-001.json studio
# 浏览器打开 http://127.0.0.1:8787
```

等待人工时不占着模型 worker；新生成不覆盖旧结果。自动跨集声纹分类尚未接入，当前局部分组建议不能当作已校准身份判断。源码附带的协议测试与提示音媒体测试不代表真实英文→中文克隆质量已经验收。

## 本地启动

需要 Python 3.11+、Node.js 22+（YouTube JavaScript 解析）、Docker Desktop、FFmpeg/ffprobe（FFV1、libx264、AAC）。使用项目虚拟环境：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/python scripts/setup_local.py  # 仅首次；生成被忽略的本地凭据

docker compose config --quiet
docker compose up -d mysql
# 等待 docker compose ps 显示 healthy
.venv/bin/python -m mlvideo.cli doctor
.venv/bin/python -m mlvideo.cli migrate
```

数据库只绑定 `127.0.0.1:3307`，应用与迁移账号分离。`config/pipeline.json` 引用环境变量，`.env` 和初始化 SQL 不进入 Git。更换账号密码必须同步实际数据库；再次运行 setup 不覆盖现有凭据。Python 安装采用 editable 模式，源码、schemas、migrations 和配置共同构成部署目录。

## 使用

```bash
.venv/bin/python -m mlvideo.cli ingest /path/to/video.mp4
.venv/bin/python -m mlvideo.cli download 'AUTHORIZED_URL'
.venv/bin/python -m mlvideo.cli history ASSET_SHA512
.venv/bin/python -m mlvideo.cli run ASSET_SHA512 N03 ffprobe --inputs inputs.json --params params.json
.venv/bin/python -m mlvideo.cli pipeline ASSET_SHA512 --recipe recipe.json
.venv/bin/python -m mlvideo.cli retry ASSET_SHA512 EXECUTION_ID
.venv/bin/python -m mlvideo.cli recover ASSET_SHA512 EXECUTION_ID
.venv/bin/python -m mlvideo.cli recover incoming ACQUISITION_ID
.venv/bin/python -m mlvideo.cli select ASSET_SHA512 source ARTIFACT_ID --reason '明确选择原因'
.venv/bin/python -m mlvideo.cli backup --out evidence/backup_unique
.venv/bin/python -m mlvideo.cli --config config/local-restore.json migrate
.venv/bin/python -m mlvideo.cli --config config/local-restore.json restore evidence/backup_unique
```

`--config` 必须在子命令前；JSON 配置中的相对数据根从项目根解析。一个数据库只允许绑定一个绝对数据根。恢复目标必须使用独立数据库、空数据根及相应账号授权。备份为完整逻辑数据库 JSON 导出和文件清单；不提供时间点恢复。

`inputs.json` 示例：`{"source":"art_..."}`；`params.json` 可以为 `{}`。配方示例见 [config/recipe.phase1.json](config/recipe.phase1.json)，将 `SOURCE_ARTIFACT_ID` 替换为导入返回的 ID。示例按 5 秒、25 fps 素材定义语句；实际媒体需提供经过标注的讲话区间、最后安全整帧切点及提示音采样数。不要把示例标注用于任意视频。

每次有效 retry 都启动新 worker、分配新版本；recover 只恢复登记。主控异常退出后，新写入会要求先恢复未完成执行/获取记录。原始请求、日志、源码快照、封存清单和产物位于 `data/videos/<SHA512>/`。history/status 可在写入期间查询。`TEST/fixture_a`、`TEST/fixture_b` 仅用于协议和故障测试。

## 自测与验收

```bash
.venv/bin/python scripts/make_fixtures.py
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python scripts/verify_phase.py --phase 1 \
  --fixtures tests/fixtures/manifest.json --out evidence/phase1_run_NEW
```

验收使用真实 MySQL、FFmpeg、子进程及 16 MiB 独立磁盘镜像，会重启本项目的 MySQL 容器并建立独立还原/磁盘故障测试数据库。只在暂停业务时运行。每次输出目录必须新建；完整下载测试还需设置 `MLVIDEO_YOUTUBE_URL` 和 `MLVIDEO_YOUTUBE_AUTHORIZATION`（授权说明），或填写素材 manifest 的对应字段。缺少它们会返回非零并标 INCOMPLETE，不以本地样例代替 YouTube 下载。

素材为本项目程序生成；`make_fixtures.py` 记录本机 FFmpeg 实际生成字节的 SHA-512 和独立预期摘要，重新生成后须重新验收。验收产物和媒体保留在本地 evidence 目录，不将旧证据当成新一轮结果。

范围、结果与限制见 [第一期实现报告](docs/verification/PHASE_1_IMPLEMENTATION_ZH.md)。后续规划见 [文档导航](docs/README_ZH.md)。

YouTube 下载使用锁定版本的 yt-dlp/EJS 和 Node。若 Python.org 安装的 Python 缺少 CA，首次 setup 会使用存在的系统 `/etc/ssl/cert.pem`；已有环境可在本地 `.env` 设置 `SSL_CERT_FILE=/etc/ssl/cert.pem`，证书校验保持启用。空数据卷初始化实测：`.venv/bin/python scripts/measure_cold_start.py --out evidence/mysql_cold_NEW`，脚本使用独立临时容器/卷，结束后清理这两个临时资源。

## 第二期候选成片

2026-09-13 第一轮已实际执行：13 GB 预算内完成前 117 秒预处理、Opus 截取修复、参考音对照及两组 12 条中文试听；88 项回归通过。双语候选成片尚未生成，待音色/分组复核，见 [第一轮报告与试听入口](docs/verification/PHASE_2_ROUND1_20260913_ZH.md)。

2026-09-12 收尾更新：已补入说话人/参考音的哈希绑定复核、逐条音色验收检查和参考音污染防护，87 项回归通过。整片分组、参考音与听感仍未通过，产物盘容量也不足以支撑当前整片估算；Phase 2 保持 INCOMPLETE，详见 [收尾报告与试听材料](docs/verification/PHASE_2_CLOSEOUT_ZH.md)。

已接入字幕清点/提取、Whisper+Silero VAD、归句、Codex 合批翻译、参考音提取、CosyVoice3、译音质检、双语布局与真实音频成片。`candidate` 串行运行并保存每批/每句不可变血缘；`verify_phase.py --phase 2` 保存实际运行与未完成项。正式视频集、自动多人识别和人工质量验收仍有缺项，不能视为第二期正式通过。部署与命令见 [workers/README.md](workers/README.md)，当前证据和问题见 [整体验证记录](docs/verification/PHASE_2_FULL_VERIFICATION_ZH.md)。

2026-09-14 工作台已基于当前分支校对，补入自动刷新、免姓名确认、数据库断线重连和磁盘余量保护；最终回归 117 项通过。见 [审查记录](docs/STUDIO_REVIEW_20260914_ZH.md) 与 [测试摘要](verification/studio-review-20260914/README.md)。真实音色及整片验收仍未完成。
