# 单元测试与真实环境集成测试计划

> 历史参考，2026-09-11 补充说明：本文原文来自 [video_interleave_v2_design_and_skeleton.zip](../sources/video_interleave_v2_design_and_skeleton.zip)，内部路径相对于包内 `video_interleave_v2/`。实现/测试状态均指该参考包，未在本次更新中重跑；当前 MySQL 串行方案见 [完整开发计划](DEVELOPMENT_PLAN_ZH.md) 和 [文档入口](README_ZH.md)。下方保留原始内容。

## 1. 测试结果如何解释

本次已运行逻辑测试和真实FFmpeg/ffprobe集成；联网YouTube和真实OCR模型默认未启用。实际结果以`evidence/test-results.xml`和`evidence/TEST_RESULTS_ZH.md`为准。模型fixture只验证协议，不评价OCR/翻译/音色效果。

默认全套测试：

```bash
python -m pytest -q --junitxml=evidence/test-results-local.xml
```

只跑无网络逻辑：

```bash
python -m pytest tests -q
```

实际媒体工具测试（不是mock）：

```bash
python -m pytest integration/test_media.py -q
```

运行独立演示，保留全部资产目录/版本/日志：

```bash
python -m vidflow.demo --out /absolute/path/new_empty_demo
```

## 2. 单元与协议用例

### 存储、版本、血缘

| ID | 条件/动作 | 预期 |
|---|---|---|
| U01 | 同一视频从两个文件名导入 | 同SHA资产目录，不同获取收据；原文件不借用外部inode |
| U02 | 改变一个输入字节再导入 | 不同SHA512资产目录，不按URL/名字合并 |
| U03 | 修改调用方原文件 | 资产源快照不被改变 |
| U04 | 篡改已入库产物 | 下游hash校验失败，不替换/重算冒充原产物 |
| U05 | 同一节点输入/参数/代码不变重试3次 | 3次实际业务调用、3个execution/version目录；输出hash可相同 |
| U06 | 一个执行失败后重试 | 失败原始文件保留，新attempt重新调用，不覆盖 |
| U07 | 8个并发执行同node/scope | 唯一、连续逻辑版本；日志seq不冲突 |
| U08 | OCR A完成后运行B | 下游绑定的A不被B替换 |
| U09 | 跨asset绑定artifact | 拒绝，不能把另一个视频字幕混入 |
| U10 | 外部文件/模型音频导入 | 保存快照、独立hash/producer，不能只有易变路径 |
| U11 | 相同文件由两个执行生成 | artifact_id和血缘不同，不以hash合并执行 |
| U12 | 显式selection改变 | 追加新selection记录；旧结果和旧run不变 |

### 插件、参数与日志

| ID | 条件/动作 | 预期 |
|---|---|---|
| U13 | 同端口输入交给两个OCR策略 | 输出相同captions契约，策略版本可区分 |
| U14 | 重复cue ID、倒序区间、错误bbox | 契约/语义校验失败，禁止下游读取 |
| U15 | 结果引用绝对路径、..、逃逸symlink | 拒绝发布 |
| U16 | params直接含API key或cookies | 拒绝，要求secret_ref |
| U17 | 插件子进程非0退出 | FAILED并保留stdout/stderr，不写伪成功 |
| U18 | worker超时 | 杀进程组、记录中断和用时，旧work保留 |
| U19 | 真子进程worker连续重试 | 新目录和新调用，不能复用旧worker.result.json |
| U20 | 参数/模型声明变化 | 每次request、manifest、日志均记录实际值 |
| U21 | 重复DAG step key、环路、未知依赖 | 运行前报错，不按输入顺序误执行 |
| U22 | 同一配方运行两次 | 配方所有节点重新调用，run独立，bindings不串 |
| U23 | 同git commit但dirty代码 | source_tree_hash/dirty/源码快照反映真实改动（生产回放需加强测试） |

### 恢复与故障注入

已实现基础用例：日志半行修复、ABANDONED恢复、活跃lease拒绝恢复、终态不得再次覆盖。生产需补全以下真实kill -9窗口，不能仅用普通异常替代断电场景：

| 窗口 | 恢复期望 |
|---|---|
| DB分配version后、request目录完成前 | 保留已分配记录/版本缺口；修复request或ABANDONED，不重用版本 |
| worker写一半图片/WAV时 | 部分文件保留、不可作为成功artifact绑定 |
| artifacts目录发布后、manifest发布前 | 原attempt不猜测成功，保留并ABANDONED/复核 |
| manifest已封存、DB终态未提交 | 校验hash后补提交；不重新执行业务旧attempt |
| DB终态已提交、events.jsonl尚未写 | 按seq投影缺失事件，无重复整行 |
| 两个进程同时recover | 只有一个取得lease；不得两次提交 |
| 远程worker迟到返回 | fencing token失效拒绝发布，需要生产扩展 |
| 磁盘写满 | FAILED，已有源/历史版本不删除、不覆盖 |

## 3. 时间线单元测试矩阵

已编码用例覆盖充足/刚好/不足/接近零/零空档、片尾、非法重叠/切点、缺失/多余译音、有理数规划、空语句、计划被篡改以及一个测试中的500个随机计划。随机子案例不另计为500项独立pytest测试。

| 输入 | 判定 |
|---|---|
| gap=8s，中文4s | H=0，中文后保留原片剩余3s |
| gap=6s，中文4s | H=0，刚好前后各1s |
| gap=3s，中文4s | H=3s |
| gap=0s，中文4s，存在安全帧界 | H=6s |
| 中文尚未开始便到安全切点 | 先定格至前1s结束，再播放中文，后1s再恢复 |
| 中文中途达到切点 | 中文连续播放，只有原片时钟暂停 |
| 两句重叠 | 必须合并/复核，不把负gap当0 |
| 句界落在帧中且找不到安全帧界 | 重新分组，不能截断词 |
| 尾部足够/不足 | 利用完整片尾；不足补最后帧，译音不丢 |
| 0中文音频/不存在文件 | 不静默生成空配音，失败 |
| 原视频多段无字幕/片头/片尾 | source coverage仍恰好一次 |
| 1000+句累计 | 全局整数/有理数无累计float漂移 |
| 29.97/23.976 | 计划器精确；参考渲染器不支持时明确拒绝，不能偷偷当30/24 |

## 4. 本次真实FFmpeg集成

生成12秒25fps素材，原音/译音使用不同频率提示音。三个原讲话区间对应中文1/3/2秒，定格0/75/50帧。实际断言：

1. 无损源视频帧hash映射到输出：原帧0..299按序一次出现；两处只重复正确前一帧。
2. 无损输出425帧，17秒，48kHz双声道PCM共816000个采样帧。
3. 按独立逻辑拼出的原PCM及译音叠加结果与输出逐采样相同。
4. H264/AAC预览完整解码通过；不要求有损编码字节/采样与源完全相同。
5. 同一个render重试，新execution/v2，实际再次执行FFmpeg，有新的进程日志。
6. 结构质量检查通过，但缺少5类AI/字幕检查，总体仍为REVIEW。

当前参考渲染器在短素材上验证正确性，按段重复解码、全PCM载入内存，不能据此宣布小时级性能已达标。未在用户Mac/NVIDIA上跑过，不提供未经测量的速度。

## 5. 真实YouTube下载集成（已提供可执行入口，未运行）

先准备对该视频及处理行为有授权的短视频URL；安装下载额外依赖、FFmpeg/ffprobe，并依据yt-dlp官方文档配置JS runtime/EJS。不要在测试报告里放cookies或API key。

```bash
python -m pip install -e '.[download,test]'
export VIDFLOW_AUTHORIZED=1
export VIDFLOW_LIVE_YOUTUBE=1
export VIDFLOW_YOUTUBE_URL='https://www.youtube.com/watch?v=<你的授权视频ID>'
python -m pytest integration/test_external.py::test_live_youtube_download_and_repeat -v
```

该测试下载两次，要求不同attempt、实际文件可用。**不假设同URL两次下载一定字节相同**。额外生产用例：同固定本地视频再导入应同SHA；换分辨率可不同SHA；下载中断无hash保留incoming；合流失败、字幕下载失败、限流、登录要求、live拒绝、超限大小和格式降级均有明确错误/策略日志。

默认未设置开关时SKIP；设置开关后缺配置、缺工具、网络/平台失败会FAIL，不会自动改成skip或fixture。网络失败不等于算法失败，报告分类但不谎报成功。

## 6. 两种真实OCR策略集成（已提供可执行入口，未运行）

将`examples/ocr_ab_live.example.json`复制到私有位置并填写真实视频、两个已实现worker的绝对argv、实际参数/模型版本以及可验证字幕文本。worker必须支持`--request --result`协议，标准输出为captions.v1。本包没有伪装成真实Paddle或Qwen的worker。

```bash
export VIDFLOW_AUTHORIZED=1
export VIDFLOW_LIVE_OCR=1
export VIDFLOW_OCR_PROFILE='/absolute/private/ocr_ab_live.json'
python -m pytest integration/test_external.py::test_real_ocr_ab_same_contract_new_executions -v
```

断言两种策略均有真实字幕结果、关键文本存在、相同契约、独立版本，并各重试一次。关键文本检查只是冒烟标准；正式质量需要golden字幕、CER、时间IoU/字框IoU、漏字幕率和耗时统计。

建议黄金集包括：单行/双行、描边/阴影、动态背景、镜头切换、短暂字幕、多个字幕带、屏幕标题、软+硬并存和无字幕。每类至少保留原图与人工确认答案；每次OCR策略升级固定相同输入版本比较。

## 7. 全链路真实环境验收（业务worker完成后的开发验收项）

以下是具体验收规格，不是本包已运行的测试声明。

### E01 正常单人硬字幕视频

准备60–180秒授权英语素材，包含充足和不足的语音空档。记录原始字幕、实际语音边界、允许修改ROI和说话者参考。依次运行N00–N20真实节点，保存run。检查SHA目录、下载/模型回执、每节点版本和完全可回溯的release。

### E02 同输入/同参数重做全链路

复制相同配方再次运行，不引用旧节点结果作为替代。每个纳入步骤出现新的execution_id；模型API发出新请求ID，TTS重做；输出相同不影响版本独立。供应商内部缓存行为不明时记录unknown，不能声称已验证重新底层推理。

### E03 OCR A/B分支

分别用两种真实OCR接同canonical/ROI，后面使用同一归句/翻译/配音/布局策略。按各自cue/utterance复合身份贯穿，不按下标混合。以显示文本、时间边界、成片字幕误差和总用时评估；保留两个branch，不覆盖。

### E04 字体与擦除可验证样本

使用授权无字幕视频，另外烧入本地有授权已知字体字幕，保留clean ground truth。流水线提取/清理/识别字体后，与已知答案比较；框外变化、残字、字符coverage和时间边界可客观检查。该合成覆盖仍是真实视频画面测试，但不能代替各类真实压制字幕的泛化评测。

### E05 多人、单词、数字、重叠语音

含两名说话者、短词、金额/单位、专名及否定句。确认reference不串人；中文独立ASR核对；重叠区必须合并或REVIEW，不偷偷丢掉其中一人。音色相似性阈值用本人授权参考集校准，不写未经验证的固定“0.8就是同一人”。

### E06 视频时基与长片

分别测试29.97CFR、VFR、非零start_pts、有旋转元数据、不同音轨起点、AAC尾填充、HDR。未实现能力必须显式拒绝；实现后检查所有插入后原音画同步、片尾完整，长片首中尾不累计漂移。不得使用-shortest截尾来满足表面duration。

### E07 故障/限流/缺依赖

真实请求超时、429、空音频、错语言、翻译JSON缺ID、输出目录磁盘满、worker崩溃、缺字体/模型、执行中kill -9。自动重试次数有限且每次新版本；最终FAIL/REVIEW保留证据。不得静默切固定音色或用原语音填中文缺口。

### E08 质量发布门禁

任何required检查SKIPPED，最终不能PASS；手动选择REVIEW结果做预览必须显式记录用途，不冒充正式通过。源视频包含静止镜头且计划额外冻结时，不误报所有静止。

## 8. 验收阈值建议

确定性指标直接硬断言：文件hash一致；输入版本固定；原帧顺序与SOURCE区间完整；译音样本无截断；无未经解释的原话/译话重叠；进程/全解码正常；required检查不缺失。

字幕/音色/翻译指标先给可配置阈值，不在缺数据时承诺准确率。可以将golden OCR CER、时间误差、字框IoU及独立ASR差异写入profile，基于真实样本选择上线门槛。对数字/金额/否定等关键内容优先用精确规则，不能被总体平均分掩盖。

每次测试报告必须保存：素材授权说明、资产SHA512、run/execution版本、所有模型/tool revisions、配置、单项结果、证据artifact及跳过原因。失败报告与成功报告同样版本化保存。
