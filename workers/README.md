# Worker 协议

第一期 worker 入口为 `python -m mlvideo.worker --request REQUEST --result RESULT`，由主控通过进程组启动闸门调用。worker 不读取数据库配置，不接收数据库凭据；模型 worker 将在后续阶段增加独立环境。

request 含 `protocol_version=1`、asset/run/execution、node/strategy/version、固定输入 `port -> [ArtifactRef]`、params、models 和 output_dir。result 必须符合 `schemas/WorkerResult.v1.json`。产物路径相对本次 work；禁止绝对路径、`..` 和所有符号链接。端口/schema 由 `contracts.STRATEGIES` 固定，主控验证后复制到 artifacts 并封存。

`TEST` 策略明确为 fixture，不代表模型推理。`MLVIDEO_TEST_FAULT` 只供验收主控注入中断，不能通过配方配置。`fill_bytes` 只用于 TEST 策略的受限磁盘验收。
