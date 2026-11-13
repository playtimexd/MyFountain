# CutLab — 本地剪辑（对标剪映）+ 图片编辑（对标 PS）

全部在本机运行：前端 React，后端 Python 引擎（FastAPI），渲染交给 ffmpeg（NVENC 硬编）。AI 能力全部走本地模型 / 本地 Ollama。

## 桌面版（独立窗口，推荐）

CutLab 是一个 Windows 桌面应用：自己的窗口、开始菜单 / 桌面快捷方式、应用图标、无控制台窗口。窗口由
[pywebview](https://pywebview.flowrl.com)（系统自带的 Edge WebView2）提供；没装 pywebview 时自动改用 Edge `--app` 窗口。

**需要**：Windows 10/11、Git、Python 3.11（3.10–3.13 可用）、Node.js ≥ 20、ffmpeg；动画 / 3D 需要 Blender 4.2+；（可选）Ollama。

### 首次安装（全新克隆）

```powershell
git clone git@github.com:playtimexd/MyFountain.git CutLab
cd CutLab
.\setup.ps1                 # 或双击 setup.cmd（PowerShell 脚本被策略禁止时）
```

`setup.ps1` 可重复运行，只补缺的部分：

1. 检查 git / Python / Node；
2. 创建 `backend\.venv`（`-SystemSitePackages` 让 venv 能用系统 Python 已装的包）；
3. `pip install -r backend\requirements.txt -r desktop\requirements.txt`（有 `uv` 时用 uv）、`npm ci`、构建界面到 `frontend\dist`；
4. 按引擎自己的规则查找 ffmpeg 和 Blender（`FFMPEG`/`FFPROBE` 环境变量或 PATH；`blender_exe` 设置或 `C:\Program Files\Blender Foundation\Blender *`），找不到会提示；
5. 创建开始菜单和桌面快捷方式 **CutLab**（`-NoShortcuts` 跳过，`-Launch` 装完直接打开）。

可选 AI 能力（Whisper、Real-ESRGAN、ComfyUI…）另有安装脚本，见下文「本地模型：现状与启用」。

### 启动 / 退出

双击 **CutLab** 快捷方式（等价于 `backend\.venv\Scripts\pythonw.exe desktop\cutlab_desktop.py`）。启动器会：

- 检查安装：依赖清单或前端源码变了就自动重装 / 重新构建（手动 `git pull` 之后也一样）；
- 启动引擎并等它就绪；如果 :8787 上已经有一个用同一数据目录的 CutLab 引擎，就直接接入；端口被别的程序（或别的数据目录的 CutLab）占用时自动换一个空闲端口；
- 关闭窗口时停止它自己启动的引擎（还有任务在跑会先确认）；接入的外部引擎保持运行；
- 再次双击快捷方式只会把已打开的窗口切到前台。

窗口菜单 **CutLab**：Check for updates…、Reload、Data folder…、Open data folder、Open log folder、Restart engine、Quit。
`--edge` 强制用 Edge 窗口；`--update` 直接打开更新页。引擎被别的脚本重启时，窗口会自动重新连上（同一数据目录、同一端口）。

- **已有安装（原来用 `start.ps1` 的机器）**：拉下这个版本后运行一次 `.\setup.ps1`（或直接双击快捷方式）。现有 `backend\.venv`、`node_modules` 和 `data\` 都会沿用；只会补装 `pywebview`（约 1 MB）并写入依赖标记。
- **Edge 回退窗口**的限制：关闭窗口时不会因为有任务在跑而先确认；任务栏图标是 Edge 的。装好 pywebview 就没有这些问题。
- 窗口里的浏览器存储（界面布局、最近打开的标签等）在 `%LOCALAPPDATA%\CutLab\webview\`，按引擎端口区分；项目本身都在数据目录里。

### 数据目录（全部在本机）

所有用户数据都在一个数据目录里：项目、媒体、素材中心、动画库（`anim\anim.db`）、`cutlab.db`、`settings.json`（含 API Key）、导出、模型、引擎日志。

| 优先级 | 来源 |
|---|---|
| 1 | 环境变量 `CUTLAB_DATA` |
| 2 | `%LOCALAPPDATA%\CutLab\desktop.json` 里的 `"data_dir"`（`setup.ps1 -DataDir "D:\CutLab Data"`，或应用里 Settings → App → Data folder…） |
| 3 | 默认：程序目录下的 `data\`（已被 `.gitignore` 忽略，永远不会被提交或被更新改动） |

更换数据目录不会搬文件：先关掉 CutLab，把旧目录内容复制过去再切换。`%LOCALAPPDATA%\CutLab\` 里还有启动器日志（`desktop.log`）和窗口的浏览器存储（`webview\`）。
引擎（`python -m app`）、MCP 服务器和启动器用同一套规则，所以三者总是看到同一个数据目录。

### 更新（git）

Settings → App → **Check for updates**，或窗口菜单 CutLab → Check for updates…：

1. `git fetch origin main`，列出新提交、改动的文件，以及更新会做什么（重装 Python 包 / `npm ci` / 重新构建界面 / 重启窗口）；
2. 有未提交的本地修改、当前不在 `main` 分支、本地 `main` 与远端分叉、或数据目录在仓库里且没被忽略时，**拒绝更新**并说明原因；未跟踪文件只提示；
3. 有任务在运行时先等它们结束；
4. **Update now**：停止引擎 → `git merge --ff-only origin/main` → 只在 `requirements.txt` / `package-lock.json` 变化时重装依赖 → 前端源码变化时构建到 `dist.next` 再替换（构建失败不会弄坏现有界面）→ 重启引擎并刷新窗口；启动器自身的代码变了就整个应用重启。
   安装或构建失败会自动 `git reset --hard` 回到更新前的提交并用旧版本重新启动（此前已确认没有本地修改；未跟踪 / 被忽略的文件不受影响）。

数据目录从不参与更新。命令行等价操作（需先关掉应用）：

```powershell
backend\.venv\Scripts\python.exe desktop\updater.py check   # 看有什么更新
backend\.venv\Scripts\python.exe desktop\updater.py apply   # 拉取 + 同步依赖 + 构建
backend\.venv\Scripts\python.exe desktop\updater.py sync    # 只同步依赖和界面（手动 git pull 之后）
```

更新源是 `desktop.json` 里的 `update_remote` / `update_branch`（默认 `origin` / `main`）。拉取用你自己的 git 凭据（SSH key 或 HTTPS），应用不会弹出密码输入框。

## 开发模式（浏览器 + Vite 热更新）

```powershell
.\start.ps1          # 同时起引擎(:8787)和界面(:5173)，并打开浏览器
.\start.ps1 -Stop    # 停止
```

引擎状态与可用能力：`GET http://127.0.0.1:8787/api/capabilities`；版本与数据目录：`GET /api/app/info`。

## 五个区域

| 页面 | 内容 |
|---|---|
| **视频剪辑** | 多轨时间线（分割/修剪/波纹/吸附/跨轨拖动/多选）、画布预览（直接拖拽缩放旋转）、属性面板（变换、调色×10、变速、音量淡入淡出、文字样式、转场、关键帧）、撤销重做、自动保存、导出 MP4/WEBM/GIF/MP3 |
| **图片编辑** | 图层（栅格/图片/文字/形状、16 种混合模式、蒙版、剪贴、图层样式、13 种非破坏调整）、选区（矩形/椭圆/套索/魔棒、羽化/扩缩）、画笔/橡皮/油漆桶/渐变/仿制/修复/涂抹/加深减淡、滤镜、裁剪、历史面板、PNG/JPG/WebP 导出、`.cutlab-psd.json` 工程 |
| **素材库** | 本地导入/拖入、分类筛选、内置素材（19 种贴纸、渐变背景、文字样式、转场、12 种滤镜、字体含自定义导入）、素材站（Pexels/Pixabay，需免费 Key）、本地电脑搜索（RAG） |
| **Video Segment**（原“链接下载”） | 选视频（素材库或粘贴链接下载：yt-dlp 覆盖 YouTube/B站/X/Facebook/IG/TikTok…，抖音/小红书带页面解析兜底）→ 在带缩略图条的时间线上拖出多个片段（或 I/O 入出点、S 拆分、自动检测：镜头/静音/句子/定长）→ 一键 **Split into N clips** 拆成多个独立视频（逐帧精确重编码或无损快速），或整组放到时间线 |
| **AI 工具** | 背景音乐生成、画质增强、去水印（含自动检测）、智能字幕、智能剪辑、抠图、素材检索、AI 剪辑助手（Agent） |

| **Animation → Make animation**（默认页，4 步） | ① 动作来源二选一：**From a video**（素材库 / 上传 / 粘贴链接，可截取一段）或 **Describe the motion**（用一句英文描述动作，NVIDIA Kimodo 本地生成，仅人形角色，1–10 秒，可“换一个变体”）+ 3D 角色（已有 / 上传 GLB·FBX / Free characters）② **Generate animation**：一键 动作捕捉 → 3D 骨架 → 重定向到角色（脚锁地、原地、循环）→ 侧面预览视频 + 游戏 GLB ③ **Preview & edit**：3D 预览 + 质量分，用一句话改（“慢 50%”“更平滑”“少滑步”“只要前两秒”…，LLM 转成设置；未配置 LLM 时用关键词规则），带编辑历史/撤销 ④ **Export**：GLB / FBX 下载、导出到引擎项目、预览视频进时间线。原来的细项工具在 “Advanced tools” |
| **Animation** | 角色库（导入 GLB/FBX/BLEND，或用 Tripo/Meshy 生成）、骨骼映射（自动建议 + 可选 LLM）、视频→动作（ViTPose++ 关键点 + 自研 3D 骨架拟合，人形/四足）、Blender 方向式重定向、足部清理/循环化、导出 GLB/FBX（游戏预设：一个 GLB 含网格+骨骼+全部动作）、透明预览可直接放进视频剪辑、发送到 my-engine；**Fur**：Blender 发丝毛发（Cycles 渲染）+ 实时壳层毛发（three.js，`shellFur.ts`） |
| **3D Models** | 模型库（角色、Tripo/Meshy 生成、上传、毛发、Asset Hub 免费 CC0 模型；**白模（clay）视图**）、**On this PC**（扫描 D 盘项目/下载/文档/OneDrive 的 .glb/.gltf/.fbx/.blend/.bvh/.dae，原地索引不复制，重复副本合并，自动分出 动画 / 带动画角色 / 绑定角色 / 静态模型，识别 Mixamo，FBX/GLB 直接预览播放）、生成（**本地 TRELLIS.2 图片/文字→带贴图 3D 模型，免费**；或 Tripo/Meshy 云端+自动绑定）、毛发；“Send to Animation / Import animation” 交给动画工作台 |
| **Free characters**（3D Models / Make animation 里） | 一键安装免费角色与白模（官方来源、带许可证）：Blender Human Base Meshes（CC0 白模）、RobotExpressive（CC0，绑定 + 14 段动画）、Fox（四足，绑定 + 3 段动画）、Cesium 人形（CC-BY）、**Unreal 引擎白模 Manny / Quinn + 26 段徒手动画**（从本机 UE5 安装里用 Unreal 自己导出，Unreal EULA：用于 Unreal 项目；做出的新动画直接匹配 UE 骨架）；带骨骼的会自动进 Animation。Unity 的 Starter Assets 白模需 Asset Store 登录，未自动化（Unity 的 Humanoid 重定向可直接用以上任一人形）。Kenney / Quaternius / Poly Pizza / Mixamo 需手动下载或填 Key（列表里有链接） |
| **素材库分类** | My media / Animations / 3D Models / Asset Hub / Built-in / Stock / Search this PC；My media 有 **自动分类**（CLIP：照片/人像/插画/角色原画/贴图/图标/Logo/截图/背景/3D 渲染；视频：实拍/口播/动画/游戏录屏/录屏/3D/动态图形；音频按名称+时长：音乐/音效/人声/环境）+ 主题与风格标签，可按分类筛选 |
| **Asset Hub**（素材库子页） | 联网资产库：Poly Haven / ambientCG / Google Fonts / CMU 动捕自动获取，Kenney / Quaternius / Mixamo 手动导入，Freesound / Poly Pizza / Sketchfab 需 Key；每个资产带许可证与署名，一键生成 CREDITS.md |

**项目（新建 / 历史）**：视频剪辑、图片编辑、Video Segment、3D Models、Animation 每个区域都有自己的项目首页（左侧 “New project” 带预设：视频=画幅+帧率，图片=画布尺寸+背景，分段=检测方式+源视频，3D=模型+起始页，动画=角色+起始页；右侧 “Recent projects” 缩略图网格，可搜索/排序/重命名/复制/删除）。编辑时自动保存到项目库，刷新或重开后回到上次的项目和位置；编辑器里点 **Projects** 随时返回。旧版只存在浏览器里的时间线/图片会在首次打开时自动转成项目。素材库、AI Studio、下载器这类“工具”区没有项目，而是 **History**：后台任务（下载、AI 处理、导入…）完成后持久记录，重启后仍在。
存储：`data/projects/<kind>/<id>/doc.json` + `thumb.*`，索引在 `data/cutlab.db` 的 `projects` 表（带 `tags`/`meta`/`parent_id`，便于以后做资产分类与溯源）；删除的项目移到 `data/projects/_trash/`。接口见 `backend/app/routers/projects.py`（`kind` = video | image | segment | model | anim）。

界面语言：英文。跨页面联动：素材库/下载器/AI 结果可一键"加入时间线"或"在图片编辑器中打开"（`frontend/src/shared/bus.ts`）。右上角齿轮 = 设置（API Key 只存本机、接口不回显）。

## 用 Claude Code / Codex 直接操作（MCP，无需在软件里填 Key）

```bash
claude mcp add cutlab -s user -e PYTHONPATH=D:\CutLab\backend -- D:\CutLab\backend\.venv\Scripts\python.exe -m app.mcp_server
```

Codex：在 `~/.codex/config.toml` 加 `[mcp_servers.cutlab]`，`command` 指向上面的 python，`args = ["-m", "app.mcp_server"]`，`cwd = "D:\\CutLab\\backend"`。33 个工具：素材/下载/分段/字幕/音乐/配音/超分/去水印/看帧/读改时间线（打开的编辑器实时同步、可撤销）/渲染单帧/导出，以及 `cutlab_api` 调用任意接口（动画、动作提取、3D 生成等）。引擎没开时会自动启动。**导出到游戏引擎**（3D Models / On this PC / Fur / Retarget & Export 里的 “Export to engine”，或 MCP `list_engine_projects` + `export_to_engine`）：自动找到本机的 Godot / Unity / Unreal / three.js 项目，按引擎写成它能直接导入的格式：Godot → GLB 到 `assets/cutlab/<名字>/`（编辑器回到前台自动导入）；Unity → FBX 到 `Assets/CutLab/<名字>/`；three.js → GLB 到 `public/assets/cutlab/`（毛发会同时拷贝 `src/cutlab/shellFur.ts`）；Unreal → FBX 到 `SourceArt/CutLab/<名字>/` + `import_to_unreal.py`（在 Unreal 里 Tools > Execute Python Script 运行，或让连了 Unreal MCP 的 Claude 运行，导入到 `/Game/CutLab/<名字>`，骨骼网格+骨架+动画序列）；也可导出到任意文件夹（GLB+FBX）。可导出：带动画的角色、毛发、Asset Hub/生成/本机模型（自动 GLB↔FBX 转换，带 CREDITS）、素材。只写进项目里的 CutLab 子文件夹，默认不覆盖（重名自动加 _2）。
**从 OneDrive 等网盘导入并自动分类**（Claude 同时连 CutLab 与 OneDrive 的 MCP 时）：`list_cloud_folders` + `import_cloud_folder`（本机同步的 OneDrive 文件夹，无需联网）；或 `import_from_links`（传 OneDrive 的 `@microsoft.graph.downloadUrl` / 下载直链 + 文件名，CutLab 不保存链接里的令牌，只允许公网 http(s)）；或 `upload_file`（base64 分块，适合只返回文件内容的连接器）。文件按类型自动进对应库：视频/音频/图片→素材库（自动分类）、.glb/.fbx/.obj/.blend→3D 模型库、.bvh/.npz→动画库、字体→字体库。另有 `categorize_media`、`list_categories`。动画：`make_animation`（prompt 文本 或 media_id 视频 → 角色动画 + GLB）、`edit_animation`（只传 clip_id + 一句话修改，如“做成无缝循环”“慢 20%”）、`auto_tune_animation`（自动调到目标分数，保留循环/原地设置，步幅最多缩到 80%）；走、跑、待机等重复动作的文字描述会自动做成循环。接口：`/api/intake/*`。
生态规划见 [docs/ECOSYSTEM.md](docs/ECOSYSTEM.md)，动画方案见 [docs/ANIMATION.md](docs/ANIMATION.md)、[docs/MOCAP.md](docs/MOCAP.md)。

## 架构

```
frontend/ (Vite+React+TS+zustand)  ──/api──►  backend/app (FastAPI, 自动注册 routers/*.py)
  modules/video   预览=Canvas 合成器 + 主时钟            routers: media assets download segment export
  modules/image   自研 Canvas2D 图层引擎                          music ai search agent jobs system
  modules/library, downloader, ai                        services/render.py  项目JSON → ffmpeg 滤镜图
  shared/ (bus, SegmentDialog, JobBar)                   jobs.py 线程池任务 + 进度/取消
                                                          data/ (cutlab.db, media, exports, index, models)
```

* **单一编辑文档**：`frontend/src/types/project.ts` 的 `Project` JSON 同时驱动预览、导出和 Agent；导出渲染器 `backend/app/services/render.py` 与预览合成器保持同一套语义（见 `docs/CONTRACT.md`）。
* **Agent**：`/api/agent/chat` 用 Ollama 工具调用（默认 `qwen3:8b`），返回经过校验的 `ops`（切分/删除/加文字/配乐/加字幕/变速…），前端一次性应用并可 Ctrl+Z 撤销。
* **设置**：`data/settings.json` 或 `PUT /api/settings`（`llm_model`、`cookies_from_browser`、`proxy`、`pexels_api_key` …）。

## 本地模型：现状与启用

开箱即用（无需下载）：ffmpeg 渲染、yt-dlp、PySceneDetect、静音分段、**程序化背景音乐**、Lanczos 放大、OpenCV 去水印/修复/GrabCut 抠图、关键词+Ollama 文本向量检索、Agent。

**本机已安装并实测（2026-11-08）**：
* Whisper `small`（`data\models\whisper`，≈464 MB）— 智能字幕、"语音句子"分段、口播剪辑。英文测试正确；中文识别未测。
* Real-ESRGAN ncnn-vulkan（`data\models\realesrgan`，≈51 MB）— 视频 320×180 → 1280×720 放大通过，48 帧约 6 秒。

可选升级（**需要先下载，脚本都在 `scripts\`**，运行前请自行确认体积）：

| 功能 | 脚本 | 下载量 |
|---|---|---|
| ACE-Step 1.5 音乐模型 | `setup_acestep.ps1`，然后 `start_acestep.ps1`（:8001，UI 自动识别） | 程序几 GB + 权重 ≈5–10 GB |
| Whisper 字幕 | `setup_whisper.ps1 -Model small`（或 UI 里"下载模型"） | small ≈484 MB（tiny 75 MB … large-v3 3 GB） |
| Real-ESRGAN AI 超分 | `setup_realesrgan.ps1` | ≈45 MB |
| 抠图 rembg / LaMa 修复 / CLIP 图像检索 | `setup_search_extras.ps1 -Rembg -Lama -Clip` | ≈170 / 200 / 600 MB |
| 本地视频生成（ComfyUI + Wan 2.2） | `bash scripts/install_comfyui.sh` | PyTorch ≈3 GB + 模型 ≈17 GB |
| 本地图片→3D（TRELLIS.2，MIT） | `bash scripts/install_comfyui.sh --3d` | ≈9 GB（int8 主模型 5.25 GB + VAE + DINOv3 + BiRefNet） |
| 更准的动作捕捉 ViTPose++ Huge | `POST /api/mocap/prepare {backend:'vitpose', variant:'huge'}` | 3.6 GB（自动优先使用已安装的最大模型） |

> 装可选包后运行 `uv pip check --python backend\.venv\Scripts\python.exe`——曾出现 `huggingface_hub`/`tokenizers` 被升级而破坏 `transformers` 版本约束的情况。

选型调研与来源见 [docs/RESEARCH.md](docs/RESEARCH.md)。

## 测试

```powershell
$env:PYTHONIOENCODING="utf-8"
backend\.venv\Scripts\python.exe backend\tests\e2e_export.py   # 导入→分段→多轨导出（需引擎在 :8787）
$env:CUTLAB_BASE="http://127.0.0.1:8787"; backend\.venv\Scripts\python.exe backend\tests\smoke_ai.py   # AI 接口冒烟（--agent 含真实 Ollama）
backend\.venv\Scripts\python.exe backend\tests\ecosystem_e2e.py   # 测试熊→重定向→透明预览→叠到视频→导出 + 游戏 GLB
backend\.venv\Scripts\python.exe backend\tests\anim_e2e.py        # 动画全链路 51 项（Blender）
backend\.venv\Scripts\python.exe backend\tests\fur_e2e.py         # 毛发：groom job、GLB 毛发属性/坐标轴、无毛部位、壳层参数保存（需引擎运行）
backend\.venv\Scripts\python.exe backend\tests\intake_e2e.py      # 导入+自动分类：本地文件夹、分块上传、路由到 3D 库、URL 安全、分类筛选（需引擎运行）
backend\.venv\Scripts\python.exe backend\tests\models_local_e2e.py # 3D 模型库、本机 3D/动画索引、Mixamo FBX 导入为动画（需引擎运行）
backend\.venv\Scripts\python.exe backend\tests\engine_export_e2e.py # 导出到引擎：Godot/Unity/Unreal/three.js 测试项目、格式转换、Unreal 导入脚本、不覆盖（需引擎运行）
backend\.venv\Scripts\python.exe backend\tests\anim_quick_e2e.py   # 4 步动画：熊视频+Sample Bear 一键生成、“慢 50%”编辑、撤销、FBX 导出（需引擎运行+GPU）
backend\.venv\Scripts\python.exe backend\tests\anim_text_e2e.py    # 文本→动画（Kimodo）：生成到 UE5 Manny、“两倍速”编辑；需本机已装 Kimodo
backend\.venv\Scripts\python.exe backend\tests\genvideo_anim_e2e.py # 本地 Wan 2.2 生成参考视频→动捕→自动调优（熊、UE5 Manny）；CUTLAB_REUSE=bear=<id>,manny=<id> 复用已有视频
backend\.venv\Scripts\python.exe backend\tests\gen3d_local_e2e.py   # 本地 TRELLIS.2：文字→参考图→3D、图片→3D，检查 GLB 网格/贴图、模型库、无需费用确认
backend\.venv\Scripts\python.exe backend\tests\mocap_synthetic.py --hard   # 动作拟合精度（合成真值）
backend\.venv\Scripts\python.exe backend\tests\mcp_smoke.py       # MCP stdio 会话
$env:CUTLAB_BASE="http://127.0.0.1:8787"; backend\.venv\Scripts\python.exe backend	ests\projects_api.py   # 项目库接口 36 项（不设 CUTLAB_BASE 时连 :8797 测试引擎）
cd backend; ..\backend\.venv\Scripts\python.exe -m unittest discover -s tests -p "hub_test_*.py"   # 资产中心 33 项
cd frontend; npx tsc -b
```

## 已知限制

* 绑定导入：只挂在骨骼上的刚体部件（低模机器人等）会自动转成蒙皮；但脚由 IK 控制、挂在根骨骼上的绑定（如 RobotExpressive）重定向后腿部不正确。Manny/Quinn/Mixamo/Fox 等常规绑定正常。
* 导出到引擎：Unreal 已在 UE 5.8（无界面命令行）里实测——生成的 `import_to_unreal.py` 导入出骨骼网格（24 骨骼，约 97 cm）+ 2 段动画（101 / 77 帧，腿部摆动 32–44°）+ 3 个材质；Unreal 会把骨骼名里的 `.L/.R` 改成 `_l/_r`（做 UE 重定向时注意）。Godot/Unity 本机未安装，只验证了文件格式与目录。
* 自动分类用的是本地 CLIP ViT-B/32：远景/风格化的动物常认成 dog（例：动物园熊视频）；类别（照片/贴图/实拍等）比主题标签可靠。音频分类只看文件名和时长。
* 抖音/小红书/部分 B 站内容依赖登录 Cookie 且随站点改版会失效；**这些站点的真实视频未做实测**。仅下载你有权使用的内容。
* 预览与导出有少量差异：锐化、>100% 音量只在导出生效；色温/暗角在预览中为近似。
* 视频：无框选、轨道不可重排。图片：无图层组/标尺/参考线，文字不能画布内直接编辑，单边最大 8192 px。
* 播放流畅度和音频输出未在真机上完整验证（测试用浏览器窗口会限制动画帧率）。
* 显存：本机 3090 Ti 常被其它程序占用，大模型请先释放显存或用 offload。
* 规划中：文字转语音、人声分离/降噪、生成式填充（ComfyUI）、补帧。
