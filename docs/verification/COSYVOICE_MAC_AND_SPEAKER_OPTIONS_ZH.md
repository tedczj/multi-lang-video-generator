# CosyVoice3 原版、Mac 配置与说话人方案核对

日期：2026-09-12。当前重点按用户最新要求收敛到 CosyVoice3 官方非量化版与 Mac 上质量/速度的平衡；不再继续 Qwen MLX 8-bit 听感测试。此前仅下载的 Qwen MLX 权重不是本轮实际推理结果，不列入胜负比较。

## 当前视频到底用了什么

| 环节 | 本机实际路径 | 精度/身份结论 |
|---|---|---|
| 中文克隆 | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`；官方代码 commit `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc` | PyTorch、CPU、`torch.float32`，不是 MLX 8-bit |
| 英/中文 ASR | Whisper small 独立 Python 环境 | 原始 PyTorch 模型，CPU、fp16=False |
| VAD | Silero VAD 6.2.1 | 独立 ONNX 模型；不是说话人身份识别器 |
| 新增说话人分析 | Sherpa-ONNX 1.13.8 + pyannote segmentation 3.0 + 英语 CAMPPlus | 本轮实际使用 FLOAT 权重图，不是 MLX；它不是官方 Community-1 完整管线 |

[ONNX 说话人模型精度审计](../../evidence/voice_model_comparison_20260912_001/onnx-precision.json)：当前 segmentation 和 embedding 浮点权重为 FLOAT，未发现量化算子；另行解包的 `model.int8.onnx` 确实包含整数权重及量化算子，但不能将“文件已准备”当作已进行实测比较。

[CosyVoice 前端图审计](../../evidence/voice_model_comparison_20260912_001/cosy-frontend-precision.json)：`campplus.onnx` 的 617 个 initializer、`speech_tokenizer_v3.onnx` 的 198 个 initializer 都为 FLOAT，未发现量化算子。LLM/flow/vocoder 的运行 dtype 另由本次基准记录；当前生成回执中的 dtype 为 `torch.float32`。因此这次用户听到的音色问题，不能归因于“用了 MLX 8-bit”。

本地已核对的官方 CosyVoice 实现按 CUDA 可用性选择 CUDA 或 CPU；此次走 CPU。没有将未经实测的 MPS、MLX 移植、vLLM 或 TensorRT 路径称作本机已验证方案。参考 [官方 CosyVoice 源码](https://github.com/FunAudioLLM/CosyVoice)。

## 搜索到的开源说话人方案

| 方案 | 本项目适用性与边界 | 本轮状态 |
|---|---|---|
| **pyannote Community-1** | 完整说话人管线，包含分段、embedding、VBx 聚类；提供便于对齐转录的 exclusive 输出。适合作为当前简化管线的主要对照 | 官方文件访问返回 HTTP 401；没有可用的已授权 HF token，未实际运行 |
| **WhisperX** | 把 ASR、词级对齐和 pyannote 说话人分析串起来；它不是与 pyannote 完全独立的另一种说话人模型 | 作为整合层候选，未宣称本机已部署/跑通 |
| **Sherpa-ONNX + 3D-Speaker** | CPU 可部署、模型组件可固定、支持说话人分段/embedding/聚类；适合本地工程化，但需要在目标素材校验分组 | 已在完整实际视频上运行；阈值敏感，结果只能标声学分组估计 |
| **NeMo Sortformer 4spk-v1** | 端到端说话人分析；该公开模型最多 4 个说话人，超过 4 人会退化，不适宜在未知人物数的整条动画上直接强用 | 仅研究；未运行 |

上述能力分别来自 [Community-1 模型卡](https://huggingface.co/pyannote/speaker-diarization-community-1)、[WhisperX 官方仓库](https://github.com/m-bain/whisperX)、[Sherpa 说话人文档](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html)、[3D-Speaker 官方仓库](https://github.com/modelscope/3D-Speaker) 和 [NeMo 模型卡](https://huggingface.co/nvidia/diar_sortformer_4spk-v1)。Community-1 权重需要在 HF 模型页接受其访问条件并取得授权；[本机实际访问记录](../../evidence/voice_model_comparison_20260912_001/community1-access.json) 是 401，没有绕过该门槛。

官方公开基准不等同于这条带音乐、音效和角色表演的动画表现。本轮没有独立完整 RTTM 真值，因此不能计算可信的全片 DER 或将某个分组数量宣布为真实人数。

## 参考音库执行情况

已新增原片完整音频提取、全片 ASR、说话人分段/聚类和 `SpeakerBank.v1`。每组收集原片区间及转录，保存多个候选。连续参考优先；短片段可按明确原区间组成 `VoiceReference.v2`，插入的间隔样本数、最终采样数、哈希、源音频与 bank artifact 都有记录。参考不足时不能自动克隆。

发现并保留的问题：

- 初始 PCM 输入的聚类阈值 0.5/0.65/0.8 分别得到 41/28/18 组；这些不是已经确认的说话人数。
- 原视频浮点解码有轻微满幅溢出。一次实测峰值 1.034457，401,280 个采样中 94 个超过满幅；直接转 PCM16 会削波。现已保留 FLOAT 原音，测量全片峰值后仅在必要时统一降幅再转 PCM16，记录增益，不剪采样。
- 处理参考电平后重新执行，默认分组为 43 组，说明声学分组仍不稳定。不能仅靠“凑够时长”把它当作可靠人物音库。
- 人工查看候选转录时发现有旁白与角色对白/音效混合的风险。增加了候选筛查，并保留被拒绝、不足或待复核的结果；这些规则不能替代更可靠的说话人模型和试听确认。

因此长参考音库当前仍是 **REVIEW 草稿**。本轮 Mac 性能基准固定使用用户已听过的 3.6 秒参考音，不把未确认的长参考混入性能配置比较，也不声称音色相似度已解决。

## Mac 基准定义

脚本：[benchmark_cosyvoice_mac.py](../../scripts/benchmark_cosyvoice_mac.py)。固定官方 checkpoint、CPU FP32、同一参考音字节、同一中文句子、原速、stream=False，比较 4/8/12 个 PyTorch 线程以及是否缓存参考条件。种子 42/43/44 各运行一次，串行执行，完整保存全部返回音频。

模型仅在基准进程开始时加载一次；加载时间、首次请求、参考条件缓存构建时间与后续生成分别记录。缓存使用官方 `add_zero_shot_spk`，只保存参考特征，不复用生成音频。每次请求仍真正调用模型。按重复轮次交替缓存/非缓存顺序，避免固定顺序的偏差；本轮仍是单台日常使用中的 Mac、单句小样本，不能推广为所有文本的吞吐保证。

`cosyvoice_mac_balance_001` 因参考候选含对白归属风险中止，不参与配置结论；`002` 只做候选准备。正式固定输入基准为 [cosyvoice_mac_balance_003](../../evidence/cosyvoice_mac_balance_003/)，结果在完成后列于下方。

## 本机实测：官方 FP32 的平衡配置

[完整基准汇总](../../evidence/cosyvoice_mac_balance_003/summary.json)。Mac 为 Apple M4 Max、64 GiB 内存；权重位于挂载盘。LLM、flow、vocoder 实测 dtype 均为 `torch.float32`。模型在基准进程中一次加载后，执行首次请求与 18 次正式生成（3 线程配置 × 缓存/非缓存 × 3 个种子），无音频结果复用。

| PyTorch 线程数 | 不缓存参考特征：生成中位数 | 缓存参考特征：生成中位数（范围） | 缓存后的 RTF 中位数 | 缓存后的 CPU 秒中位数 |
|---:|---:|---:|---:|---:|
| 4 | 7.03 秒 | 6.59 秒（5.90–7.30） | 1.328 | 29.55 |
| **8** | **6.32 秒** | **6.02 秒（5.34–6.67）** | **1.214** | **52.00** |
| 12 | 7.17 秒 | 6.55 秒（5.48–6.78） | 1.317 | 79.96 |

- 本次进程初始化 61.31 秒；首次请求单列为 9.05 秒，产生 6.44 秒音频，不能与不同种子的后续音频直接相减推算冷启动损失。
- 参考特征缓存建立耗时 0.49 秒。8 线程下相对不缓存的生成中位数减少约 0.29 秒；更大的收益是避免每句重复初始化模型。
- 进程峰值 RSS 约 8.61 GiB，包含完整模型、前端和运行内存，不等同于权重文件大小。
- [解码采样比较](../../evidence/cosyvoice_mac_balance_003/decoded-audio-comparison.json)：缓存/非缓存同线程同种子的 9 组结果，解码 FLOAT PCM 完全相同。WAV 文件哈希不同不代表声音不同，文件级哈希与实际采样比较分开保留。

**当前推荐：官方 CosyVoice3、CPU FP32、8 个线程，模型在一次连续任务中保持加载，并按固定参考音哈希缓存条件特征，串行生成。** 4 线程适合希望降低 CPU 占用的情形：本次约慢 9%，但 CPU 累计时间少约 43%。12 线程在这次样本上没有优势。

该推荐没有使用量化，也没有声称 MLX/MPS 是官方已支持的 Mac 路径。当前业务 N11 的进程隔离仍会按执行启动并加载模型；**一次加载多次生成目前已在基准脚本实测，尚未把它冒充为生产常驻服务已经部署**。真正接入常驻 worker 时仍须保留串行调用、超时/取消、请求身份和不可变回执，不缓存生成结果来绕过 retry。

同配置不同采样轮次的原始试听（没有挑选最佳样本）：[种子 42](../../evidence/cosyvoice_mac_balance_003/results/fixed_t8_cached_s42.wav)、[种子 43](../../evidence/cosyvoice_mac_balance_003/results/fixed_t8_cached_s43.wav)、[种子 44](../../evidence/cosyvoice_mac_balance_003/results/fixed_t8_cached_s44.wav)。它们使用同一个已知短参考，属于运行配置比较；音色相似度仍待参考音选择和用户试听验证。

## 当前交付与未完成项

- 中文字幕已按用户要求改为画面内：默认英文下方，贴近下缘放不下时改为英文上方；不增加黑边。[当前 1280×720 预览](../../evidence/youtube_Ye33eY4UNtY_inline_001/preview.mp4)，该版布局更新时原 PCM 音轨保持一致。
- 新增代码及相关媒体回归 50 项通过；模型基准、主干与前端精度核对、参考库失败/重建记录均保留。
- **音色不像的问题未标为解决。** 当前原版已经是非量化，量化不是本次直接原因；参考音库的分组可靠性仍不足，尤其不能把夹有对白/音效的片段简单拼长。
- Community-1 官方权重的真实对照尚因 HF 访问授权受阻。当前 ONNX 组件流程不能冒充 Community-1 原版运行。Qwen MLX 8-bit 按用户最新要求停止扩展测试。
