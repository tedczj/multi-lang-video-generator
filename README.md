# multi-lang-video-generator

Mac 主控、MySQL 8.4、单写入者和独立 worker 的版本化媒体流水线。第一期实现媒体执行，不包含真实翻译、克隆语音或字幕制作；提示音演示的业务 QA 始终为 REVIEW。

## 本地启动

需要 Python 3.11+、Docker Desktop、FFmpeg/ffprobe（FFV1、libx264、AAC）。使用项目虚拟环境：

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
