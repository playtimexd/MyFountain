# CutLab 调研：开源工具 & 本地模型

> 标注说明：**【已查证】** = 本次联网搜索/读取项目主页确认；**【经验】** = 来自已有知识，采用前请再核对最新版本/许可证。

## 1. 能不能直接 fork 一个开源剪辑器？

| 项目 | 许可 | 结论 |
|---|---|---|
| [OpenCut](https://github.com/OpenCut-app/OpenCut) | MIT | 【已查证】定位"开源剪映"。`main` 正在从零重写（Rust core + 插件化 + 计划中的 MCP/headless API），**暂不接受外部贡献**，旧版 classic 仍在线。不适合当 fork 基底（地基在变），但值得跟踪：它的 MCP/headless 方向和我们的 Agent 化思路一致。 |
| FreeCut / voidcut / Timeline Studio | 多为 MIT | 【已查证】纯浏览器、WebCodecs/WebGPU 路线，多轨时间线。可作 UI 参考，成熟度参差。 |
| omniclip、designcombo/react-video-editor | MIT | 【已查证】React/WebCodecs 编辑器，可参考时间线交互。 |
| Remotion | 源码可用，商用需付费 | 【经验】适合"代码生成视频"，不适合交互式剪辑器内核。 |
| Photopea | 闭源 | 【已查证】PS 级网页修图，但不能集成。 |
| GIMP / Krita / Glimpse | GPL | 【已查证】桌面级开源修图，GPL 不便嵌入；可做功能对照。 |

**决策**：自己做一个"轻内核"——前端用 React + Canvas 做预览与编辑，**渲染一律交给 ffmpeg**（NVENC 硬编，所见即所得由同一份项目 JSON 驱动），AI 能力放在本地 Python 引擎里。这样不依赖任何一个还在重构的上游。

## 2. 能力 → 工具/模型矩阵（针对 RTX 3090 Ti 24GB，实际常只空闲 ~8GB）

| 能力 | 首选（本地） | 备选 / 升级 | CutLab 现状 |
|---|---|---|---|
| 渲染/转码 | **ffmpeg**（NVENC） | — | ✅ 已实现 |
| 链接下载 | **yt-dlp**（1000+ 站点，含 YouTube/B站/X/Facebook/IG/TikTok）【已查证】 | 抖音/小红书需登录 Cookie 或页面解析兜底；Playwright 登录态抓取【已查证有社区方案】 | ✅ yt-dlp + 抖音/小红书解析兜底 |
| 镜头分段 | **PySceneDetect** | TransNetV2（深度学习镜头边界，更准）【经验】 | ✅ PySceneDetect |
| 静音/语音分段 | ffmpeg silencedetect | Silero VAD【经验】 | ✅ |
| 语音转字幕 | **faster-whisper**（small→large-v3-turbo） | 中文更强：FunASR / SenseVoice / Paraformer；WhisperX 词级时间戳【经验】 | 🔧 适配器已写，**需下载权重** |
| 背景音乐生成 | **ACE-Step 1.5**（MIT，REST API，<4GB 起步，XL 约 12–20GB）【已查证】 | MusicGen/AudioCraft、Stable Audio Open、YuE（带歌词，22–28GB 级）【已查证/经验】 | ✅ 程序化合成兜底 + 🔧 ACE-Step 适配器 |
| 画质增强/放大 | **Real-ESRGAN**（ncnn-vulkan 或 PyTorch）【已查证：逐帧会闪烁，需时序处理】 | Video2X、REAL Video Enhancer【已查证】；扩散类 SeedVR2/STAR 效果好但吃显存；RIFE 补帧【经验】 | ✅ lanczos 兜底 + 🔧 Real-ESRGAN 适配器 |
| 去水印 | ffmpeg `delogo`（静态角标）+ OpenCV inpaint | **LaMa**（单图修复）；**ProPainter / E2FGVI**（视频修复）+ SAM 2 传播掩码【经验】 | ✅ delogo/OpenCV + 自动检测静态水印区域 |
| 抠图 | **rembg**（U²-Net/ISNet/BiRefNet） | RMBG-2.0（非商用许可）、Robust Video Matting（视频）、SAM 2 交互分割【经验】 | ✅ GrabCut 兜底 + 🔧 rembg |
| 素材检索 RAG | **Qwen3-VL-Embedding**（Apache-2.0，2B/8B，文本/图/视频同一向量空间）【已查证】 | SigLIP 2 / OpenCLIP 逐帧向量（便宜，看不到运动和语音）【已查证】；nomic-embed-text 文本向量（本机已装）；LanceDB / SQLite 存向量【已查证 LanceDB 内置 OpenCLIP】 | ✅ 文本向量+FTS5 混合检索 + 🔧 OpenCLIP/VLM 描述 |
| AI Agent | **Ollama + qwen3**（本机已有 qwen3:8b、qwen3.8），function calling | — | ✅ 工具调用 → 输出可撤销的剪辑操作 |
| 文字转语音/配音 | CosyVoice / F5-TTS / Kokoro【经验】 | — | ⏳ 规划中 |
| 人声分离/降噪 | Demucs、DeepFilterNet【经验】 | — | ⏳ 规划中 |
| 生成式填充/文生图 | ComfyUI API（SDXL/Flux inpaint）【经验】 | — | ⏳ 规划中（图片编辑器已预留 inpaint 接口） |

## 3. 来源（本次联网查到的）

- OpenCut：<https://github.com/OpenCut-app/OpenCut>，及 [OpenCut explained](https://mer.vin/2026/07/opencut-explained-open-source-capcut-alternative-classic-editor-vs-ground-up-rewrite/)
- 浏览器端 WebCodecs 编辑器：[FreeCut](https://github.com/fengmk2/freecut)、[designcombo/react-video-editor](https://github.com/designcombo/react-video-editor)、[Timeline Studio 介绍](https://dev.to/martindelophy/how-i-built-a-local-first-ai-video-editor-with-webcodecs-webgpu-and-wasm-4lkn)
- ACE-Step 1.5：<https://github.com/ace-step/ACE-Step-1.5>（MIT；`uv run acestep-api` 起 REST 服务；显存分级见其 README），[AMD 博客](https://www.amd.com/en/blogs/2026/commercial-grade-ai-music-generation-on-amd-ryzen-ai-and-radeon-ace-step-1-5.html)
- 视频超分：[REAL Video Enhancer](https://github.com/TNTwise/REAL-Video-Enhancer)、[Video2X](https://github.com/k4yt3x/video2x)、[Real-ESRGAN 视频闪烁讨论](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/real-esrgan-basicvsr-ott-archive-upscaling)
- 多模态检索：[Qwen3-VL-Embedding 论文](https://arxiv.org/pdf/2601.04720)、[视频嵌入模型对比](https://mixpeek.com/guides/video-embedding-models)、[LanceDB OpenCLIP](https://docs.lancedb.com/integrations/embedding/openclip)
- yt-dlp 支持站点：<https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md>（官方提示：站点改版会导致暂时失效，抖音/小红书最易受影响）

## 4. 已知风险

1. **抖音/小红书**：yt-dlp 支持不稳定，页面解析兜底也会随改版失效；多数情况需要登录 Cookie。已在设置中提供 `cookies_from_browser`。
2. **版权**：下载功能只应用于你有权使用的内容；Pexels/Pixabay 素材站需自行申请免费 API Key。
3. **显存**：本机 3090 Ti 常被其它程序占用 ~16GB，大模型（ACE-Step XL、扩散超分、Qwen3-VL-8B 嵌入）需要先释放显存或使用 offload/量化。
4. **许可证**：MusicGen 权重、RMBG-2.0 等为非商用；ACE-Step 1.5、Qwen3-VL-Embedding、faster-whisper 可商用【已查证 ACE-Step/Qwen3-VL-Embedding】。
