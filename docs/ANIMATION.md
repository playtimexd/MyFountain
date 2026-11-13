# 动画功能方案：视频 → 动作 → 绑定到新角色 → 游戏动画资产

> 标注：**【已查证】** 本次联网确认；**【经验】** 来自已有知识，采用前需核对；**【未验证】** 没找到可靠来源。

## 0. 直接回答你的问题

| 问题 | 结论 |
|---|---|
| 熊走路视频 → 复刻到我的熊模型 → 导出游戏动画，能做吗？ | **能做出可用的动画，但不是"1:1 像素级"**。单目视频缺深度，会有脚滑、尺度、遮挡问题，需要自动清理（足部锁定、平滑、循环化）+ 人工微调。 |
| Meta 模型 vs NVIDIA 模型，哪个好？ | **两者都是人形（人体）模型，不能直接用于熊。** 人形：NVIDIA **GEM** 更适合"视频→动画"（时序 + 世界坐标 + 手脚）；Meta **SAM 3D Body** 是单张图的人体网格恢复（精度好、可用关键点提示），无原生视频时序。熊（四足）要走动物路线，见 §2。 |
| 类似 Blender 的工具可以吗？ | **可以，Blender 就是重定向+导出引擎**：无界面批处理（`blender -b -P`），本机已装 Blender 5.2；输出 GLB/FBX（Unity/Unreal/Godot/three.js 通用）。 |
| 动画能搞到视频软件里吗？ | **能**：把动画用 Blender 渲染成透明背景短视频（含 alpha）→ 作为普通素材/片段放上时间线；以后可加 three.js 的 3D 图层。 |
| 做成动画资产数据库？ | **能**：SQLite + 向量检索（标签/描述/预览图），记录来源视频、骨架类型、是否循环、是否 root motion、导出文件。 |
| 参考视频来源能用 segment 工具从网上获取吗？ | **能，已有链路**：链接下载 → 智能分段（镜头/静音/固定）→ 选一段做动作源。 |
| 能用 Seedance API Key 或本地视频模型生成参考视频吗？ | **能**，作为"参考视频生成"来源：写提示词 → 生成固定机位的侧面行走视频 → 动作提取。Seedance 的**官方接口我没查到**（只有第三方网关文档），接入必须以官方文档为准。本地视频模型走 ComfyUI API；本机空闲显存 ~8GB，只有 Wan 2.2 5B（带卸载）可能跑得动【单一来源，未验证】。 |
| Opus 5.5 能加入"训练动画模型"吗？ | **不能当动作模型用**（它不输出骨骼动作，也没法在这里训练）。**可以用 API Key 当"导演/质检员"**：①骨骼映射建议（源骨架 ↔ 目标骨架）②看渲染帧做质检（脚滑、方向反了、比例失真）并给出修正建议③给动作打标签/写描述入库④写 Blender 清理脚本⑤编排整条流水线。设置里填 Key 即启用，默认走本地 Ollama。注意：用云端会把画面帧发给 Anthropic。 |
| 没有熊模型怎么办？Meshy 还是 Tripo？ | **熊走 Tripo**【已查证官方文档】：Tripo v3 自动绑骨支持 biped / **quadruped** / hexapod / avian / serpentine 等，并有预设 `preset:quadruped:walk`；**Meshy 绑骨只支持人形**（文档写明不支持非人形）。Tripo 没有公开价格表，界面提供"查余额"；Meshy 有积分表。两者都只在你勾选确认费用后才调用。 |
| 能不能让 Claude / Codex 通过 MCP 直接操作软件？ | **能。** 见 §6：MCP 服务器 + "桥接"，Claude 改的时间线会实时出现在你打开的编辑器里。 |

## 1. 调研：视频 → 3D 动作的模型

### 人形
| 模型 | 说明 | 许可/注意 |
|---|---|---|
| **NVIDIA GEM / GEM-X**（原 GENMO） | 视频/2D 关键点/文本/音频 → 人体动作；GEM-X 用 SOMA 骨架，**77 关节含手和脸**，附 Unitree G1 机器人重定向。【已查证】<https://github.com/NVlabs/GENMO> <https://github.com/NVlabs/gem-x> | 代码 Apache-2.0，**模型权重是 NVIDIA Open Model License**；SMPL 版另受 SMPL 许可约束（商用需另查）。 |
| **Meta SAM 3D Body** | 单图全身网格恢复（身体+手+脚），可用 2D 关键点/掩码提示，基于 MHR 骨架。**无原生视频时序**，单图推理耗时数秒。【已查证】<https://github.com/facebookresearch/sam-3d-body> | 模型是 SAM License；MHR 许可第三方称宽松【未验证】。 |
| 轻量基线：MediaPipe Pose 等 | 逐帧 3D 地标，几十 MB，质量一般 | 【经验】 |

**结论**：做游戏动画首选 **GEM(-X)**，Meta 3D Body 适合单帧精修/手部。商用前必须读权重许可。

### 四足 / 动物（熊属于这类）
| 方案 | 说明 | 状态 |
|---|---|---|
| **MoCapAnything**（2025-12） | 单目视频 + **任意带骨骼的 3D 资产** → 直接输出驱动该资产的 BVH，跨物种重定向。【已查证】<https://arxiv.org/abs/2512.10881> | **没找到代码/权重链接**，暂不能依赖 |
| **AnimaX**（2025-06） | 基于 Wan2.1 的视频-姿态联合扩散，给带骨骼网格生成动画，约 6 分钟/个。【已查证】<https://github.com/anima-x/anima-x> | 仓库未核实许可与权重完整性 |
| SMAL 系（SMALify、AniMer CVPR25、Animal Avatars） | 四足形状+姿态，SMAL 只覆盖少数科（猫/犬/马/牛/河马），**熊要近似** | 【已查证】存在，细节【未验证】 |
| **ViTPose++ (AP-10K) 2D 动物关键点** + 本项目的"骨架拟合" | transformers 已在环境里；先得 2D 关键点，再对**目标熊的骨架**做优化拟合（见 §3） | **推荐的落地路线**，权重约几百 MB，需确认后下载 |

**重点**：没有现成的"熊视频 → 熊模型"一键模型。落地路线 = **2D 关键点（ViTPose++/SuperAnimal）→ 用目标骨架做 3D 拟合（我们自己写，GPU 上用 torch 优化）→ Blender 重定向导出**。MoCapAnything/AnimaX 一旦有可用开源实现再作为可选后端。

## 2. 流水线

```
参考视频来源                 动作提取                        绑定到新角色（Blender 无界面）          产物
 ├ 链接下载+分段 (已有)  ─►  ① 主体检测/跟踪 (SAM2/YOLO)  ─►  ④ 骨骼映射 (规则+可选 LLM)       ─►  GLB / FBX 动画
 ├ 本地文件               ② 姿态估计                        ⑤ 方向式重定向 (每根骨骼对齐方向)       ─►  透明背景预览视频(可进剪辑)
 └ Seedance / 本地视频模型     人形: GEM / SAM 3D Body        ⑥ 清理: 足部锁定、平滑、循环化、      ─►  动画资产库(SQLite+检索)
   生成 (固定机位侧视)         四足: ViTPose++ → 骨架拟合        root motion 就地/保留
                           ③ 标准动作文件 motion.npz        ⑦ Opus/Claude 质检(可选, API Key)
```
**方向式重定向**（对任意骨架通用）：对每根被映射的骨骼，让它从父关节指向子关节的方向对齐源动作里对应的方向，再用 IK 做脚部落地；不依赖两个骨架拓扑相同。

**参考视频的拍摄要求**（越符合越准）：固定机位、全身入镜、侧面/四分之三视角、背景简单、动作循环（走/跑各 3–5 步）。生成视频时把这些写进提示词。

## 3. 数据模型与接口约定（给实现者）

目录：`data/anim/{rigs,clips,previews,exports}`；Blender 脚本在 `backend/blender/*.py`，由 `services/anim/blender.py` 以 `blender.exe -b -P script -- args.json` 调用（exe 路径：设置 `blender_exe`，空则自动探测 `C:\Program Files\Blender Foundation\Blender *\blender.exe`）。

**标准动作文件 `motion.npz`**（提取阶段输出，重定向阶段输入）：
`fps` (float)、`skeleton` ('coco17'|'ap10k17'|'smpl24'|'soma77'|'custom')、`joint_names` (J,)、`joints3d` (T,J,3) 米、Y 向上、`conf` (T,J)、`root` (T,3) 根位移、可选 `rot` (T,J,4) 四元数、`meta` JSON（来源媒体、帧范围、相机假设）。

**数据库表**（`data/anim/anim.db`）：`rigs(id,name,file,kind,bones_json,bone_map_json,thumb,created)`；`clips(id,name,rig_id,skeleton,source_json,fps,frames,duration,loop,root_motion,tags_json,caption,files_json,preview_media_id,embedding,created)`。

**HTTP**（前缀 `/api/anim`、`/api/mocap`、`/api/videogen`，均为 job 风格，见 CONTRACT.md）：
```
GET/DELETE /api/anim/rigs[/{id}]   POST /api/anim/rigs/import {path|mediaId} -> job   PUT /api/anim/rigs/{id}/bonemap {map}
POST /api/anim/rigs/{id}/suggest-map {use_llm?:bool} -> {map, confidence, notes}
GET/DELETE /api/anim/clips[/{id}]  POST /api/anim/clips/import {path} -> job (BVH/FBX/GLB)   GET /api/anim/search?q=
POST /api/anim/retarget {clipId, rigId, options:{in_place,loop,scale,foot_lock,fps,smooth}} -> job(result {clip})
POST /api/anim/export  {clipId, format:'glb'|'fbx', options:{bake,all_actions}} -> job(result {file})   GET /api/anim/file/{clipId}.{glb|fbx}
POST /api/anim/preview {clipId, camera:'side'|'front'|'three_quarter'|'orbit', size, bg:'transparent'|'#rrggbb', frames?} -> job(result {media})  # webm/alpha or mp4
POST /api/anim/annotate {clipId, use_cloud?:bool} -> job   POST /api/anim/review {clipId, use_cloud?:bool} -> job(result {issues[], suggestions[]})
POST /api/mocap {mediaId, start?, end?, subject:'humanoid'|'quadruped'|'auto', backend?:'auto'|'gem'|'sam3d'|'vitpose', rigId?} -> job(result {motion_file, clipId?})
GET  /api/mocap/backends -> [{id,name,available,note,size}]    POST /api/mocap/prepare {backend} -> job   # downloads weights ONLY when called
POST /api/videogen {prompt, image_mediaId?, duration, aspect, provider?:'auto'|'seedance'|'local'} -> job(result {media})   GET /api/videogen/providers
```
Opus/Claude 通过 `services/llm.py`（已实现：ollama / anthropic 二选一，Key 来自设置，**不在日志与接口里回显**）。

## 3.5 真实视频实测（2026-11-08，Wikimedia 棕熊视频 12.4–15.2 s，CC BY-SA 3.0，仅测试）
| 阶段 | 修复前 | 修复后 |
|---|---|---|
| 2D 检测覆盖 / 平均置信度 | 100% / 0.72 | 同 |
| 绑定后：全身翻滚 | 有（侧拍时 3D 左右髋深度歧义 → 身体横轴翻 180°） | 无（`retarget.py: stabilize_lateral`） |
| 相机俯角 | 假设水平 | 自动估计（0/15/30/45° 短拟合取最优）= 45°；`fit3d` 支持 `ground_pitch_deg` + 骨盆高度先验 |
| 步态周期 | 未检出 | 1.23 s |
| 脚滑（% 身高/秒） | 156 | 12.7 |
| 绑定评分 | 28.9 | 76.7 |
已知局限：俯拍时姿态精度（PA-MPJPE）在合成测试里仍有 16–21%，自动俯角只是粗估；镜头跟拍平移会让"行进速度"失真（游戏导出用原地动画，影响不大）。新增 2D 左右标签纠正（Viterbi，`pipeline.fix_lr_swaps`），本段视频 2D 标签本身是一致的。

## 3.6 毛发（Animation → Fur）
同一份"毛发场"驱动两条路：
| | 离线（过场/预告片） | 实时（游戏） |
|---|---|---|
| 做法 | Blender Geometry Nodes 发丝（Cycles 渲染） | 壳层毛发 shell fur：网格多画 N 层，沿蒙皮法线外推，片元按发丝格点丢弃 |
| 代码 | `backend/blender/fur_groom.py` | `frontend/src/modules/anim/fur/shellFur.ts`（只依赖 three，可直接拷进 my-engine） |
| 示例熊 | 20.7 万根，OptiX 渲染约 6–8 s/帧 | 2 个网格 × 24 层 = 48 个额外 draw call，距离 LOD 自动减层 |

- **毛发场**（逐顶点，写进 GLB 自定义属性）：`_FUR_LEN` 长度（网格单位）、`_FUR_DIR` 梳理方向（网格空间、glTF Y-up；注意 Blender 导出器不会转换自定义属性的坐标轴，所以脚本里预先转换）、`_FUR_DENS` 密度 0..1。three.js GLTFLoader 读成 `_fur_len/_fur_dir/_fur_dens`。
- **自动规则**：按骨骼名分区（爪/脸/耳短，颈/背长，背脊与腹部再加长）；按材质分类（nose/eye/claw… 或无贴图的近黑材质 = 无毛；muzzle/face = 短毛）；方向 = 远离头部 + 向下，投影到表面后平滑。
- **跟随动画**：发丝在 *静止姿态* 网格上撒点（避免变形时面积变化导致"跳毛"），再用 Sample Nearest Surface 钉到变形后的表面；查找限制在同一个网格岛内（重叠的身体部件接缝处不会串到另一块，否则会出现秃斑）。发丝放在独立的 Curves 对象上（网格对象输出的曲线在 Cycles 里不会渲染成毛发）。
- **输出**：`data/anim/fur/<id>/`：`*_fur.glb`（带毛发场 + 原动画）、`*_fur_render.png`、`*_groom.blend`（毛发绑在骨架上，任何重定向动作都能直接渲染带毛版本）、`fur.json`（`cutlab.fur/1`，含壳层参数，引擎按它设置）。
- **接口**：`GET /api/anim/fur/sources`，`POST /api/anim/fur {source, params, render, shell}`（job），`GET/DELETE /api/anim/fur/{id}`，`PUT /api/anim/fur/{id}/shell`，`GET /api/anim/fur/{id}/file/{name}`。
- **预览页**：`http://localhost:5173/fur-lab.html?glb=<url>&view=side|front|three_quarter|close&t=<秒>&fur=0|1`（全屏，用于截图/QA）。
- **局限**：壳层毛发在剪影边缘偏"绒"，侧面掠射角能看到层间缝隙（可加 fins 鳍片补，未做）；颜色用 tint 乘原材质色，不读 Blender 的 melanin；示例熊由重叠椭球组成，真实模型（Tripo/Meshy 单一网格）效果会更连续；发丝动画渲染（逐帧 mp4 进剪辑）尚未接入，目前只出单帧。

## 4. 进入视频软件
预览视频（透明 WebM/VP9 alpha）作为普通 `video` 素材入库（origin='anim'）；导出时 ffmpeg 需用 `-c:v libvpx-vp9` 解码器读取以保留 alpha（render.py 对 origin='anim' 的 webm 素材处理）。动画库里的"加入时间线"= 渲染预览 → emit `cutlab:add-to-timeline`。

## 5. 风险
单目重建的物理不准（脚滑/穿地/尺度）；SMPL/SAM/NVIDIA 权重许可对**商用游戏**有限制，使用前逐个核对；云端 LLM 会上传画面帧，默认关闭；Seedance/本地视频生成的内容版权与平台条款需自行确认。

## 6. MCP：让 Claude / Codex 直接操作 CutLab
* **MCP 服务器**（stdio）：`D:\CutLab\backend\.venv\Scripts\python.exe -m app.mcp_server`。把 CutLab 的能力暴露成工具：素材库搜索/导入、链接下载、分段、字幕、音乐/配音、超分、去水印、抠图、动画库与动作提取、导出，以及**时间线编辑**。
* **桥接（bridge）**：时间线状态在浏览器里，所以引擎加 `/api/bridge/*`：编辑器定时上报项目快照；MCP 的 `get_project` 读快照，`apply_ops` 把操作入队，编辑器取走并用自己的 `applyOps` 应用（一次撤销步），再回报结果。**你打开编辑器时，能实时看到 Claude 在改时间线**，Ctrl+Z 可撤销。
* 配置示例：Claude Code：`claude mcp add cutlab -- D:\CutLab\backend\.venv\Scripts\python.exe -m app.mcp_server`；Codex：`~/.codex/config.toml` 里加 `[mcp_servers.cutlab]`（`command`/`args` 同上）【格式按各自最新文档核对】。
* 安全：MCP 工具默认只绑定本机；会花钱/发云端/删除的工具（云端 LLM、Seedance、删除素材）单独标注并需要调用方确认。

## 3.7 Kimodo 试验（NVIDIA 文本→动作，2026-11-09）
本机已有 Kimodo（`Downloads\kimodo-venv`，权重 Kimodo-SOMA-RP-v1，NVIDIA Open Model License；文本编码器 Llama-3-8B 放 CPU 运行，显存 < 3 GB）。5 个提示词各生成 4 秒（每条约 35–45 秒，大部分是加载模型），导出 BVH（`--bvh --bvh_standard_tpose`）→ CutLab 导入 → 重定向到 UE5 Manny：

| 动作 | 质量分 | 滑步 %身高/秒 | 抖动 p95 |
|---|---|---|---|
| UE5 Walk Fwd（Epic 动捕，参照） | 99 | 3.5 | 1.1° |
| Kimodo 受击后退 | 90 | 0.0 | 0.04° |
| Kimodo 潜行 | 90 | 2.4 | 0.5° |
| Kimodo 双手剑下劈 | 88 | 5.5 | 0.1° |
| Kimodo 步行 | 74 | 17.2 | 0.8° |
| Kimodo 走-转身-走回 | 55 | 33.4 | 0.6° |

结论：动作语义都对、非常平滑；动作类（受击/攻击/潜行）质量接近商用动捕，移动类（步行、转身）滑步偏高——标准步行/跑仍用 Mixamo / UE5 自带动画，Kimodo 用来做“库里没有的定制动作”。图：`docs/kimodo_spike.png`。
试验中修了两个通用问题：FBX 导入不再删除真实末端骨骼（UE 的 `head`、`ball_l` 之前会丢）；骨骼自动映射识别 UE 辅助骨（twist/ik）和 SOMA 命名（LeftLeg=大腿、LeftShin=小腿、Shoulder=锁骨），脊椎顺序修正。


### 3.7.1 已接入：Make animation → Describe the motion（2026-11-10）
- 后端 `app/services/anim/kimodo.py`：自动找到本机 Kimodo（`kimodo_gen`，独立 Python 环境，不污染 CutLab 的 venv）；只用 Kimodo-SOMA-* 权重（NVIDIA Open Model License，可商用），从不选 SMPL-X（R&D 许可）；离线运行（HF_HUB_OFFLINE=1），文本编码器默认放 CPU（设置项 `kimodo_text_encoder_device` 可改 cuda）。
- `POST /api/anim/quick/generate-text {prompt, rigId, duration, seed}` → Kimodo BVH → 源动作（标签 kimodo、caption=提示词）→ 与视频路线相同的重定向/预览/GLB；第 3、4 步（一句话修改、导出到引擎）完全复用。`GET /api/anim/quick/text/status`。
- MCP：`make_animation`（prompt 或 media_id）、`edit_animation`。
- 实测：“挥剑下劈”→ UE5 Manny 92 秒，90 帧，质量 81；“施法双手前推”质量 90、滑步 0；“两倍速”编辑 2.97→1.47 秒。

### 3.7.2 三视图预览 + 自动调优（2026-11-10）
- 第 3 步预览改为 **front | side | top 三视图视频**（`services/anim/multiview.py`，Blender Workbench 正交相机，跟随身体，深色背景，进素材库可放时间线）；`POST /api/anim/quick/multiview {clipId}` 可重渲。修复取景：UE 骨架的 `root` / `interaction` / `center_of_mass` / `ik_*` 不随身体移动，原地动画时会把镜头拉到 9 m 宽——取景与“悬空”指标现在只用身体骨骼（`metrics.body_bones`）。
- **Auto-tune to 90+**（`POST /api/anim/quick/autotune`）：按扣分最多的问题（滑步、穿地、悬空、抖动、循环接缝）依次尝试改设置（锁脚、贴地、缩小步幅、加平滑、裁到整数步态周期、取消强制循环），保留最好的，达到目标即停（最多 10 次，试验结果自动删除，只保留最终版并重出三视图+GLB）。
- 评分修正（更公平）：循环接缝只对循环动画扣分；“悬空”改用全身最低点（翻滚时背着地不算悬空），并排除自由落体帧（真正的跳跃不算悬空）。
- 实测：Kimodo“跑-跳-翻滚”→ UE5 Manny 原 50 分，自动调优 3 次 → 91.7（步幅缩到 81%，滑步、穿地大幅下降）。
- 第 1 步新增第三种来源 **Generate a video**：用视频模型（本地 ComfyUI 或 Seedance）生成参考视频，再走动作捕捉（四足动物也适用）。

### 3.7.3 本地视频模型（ComfyUI + Wan 2.2）与姿态检查（2026-11-10）
- **CutLab 自带 ComfyUI**：`data/comfyui`（官方 ComfyUI 代码 + 独立 Python 3.12 / PyTorch 2.11 cu128 + Wan 2.2 TI2V-5B fp16、UMT5-XXL fp8、Wan 2.2 VAE，共约 20 GB，Apache-2.0）。安装脚本 `scripts/install_comfyui.sh`。不改动 Comfy Desktop（`extra_model_paths.yaml` 只读复用它的模型目录）。生成参考视频时 CutLab 自动在 127.0.0.1:8188 无界面启动（`services/providers/comfy_local.py`），无需手动打开。
- Make animation 第 1 步 **Generate a video**：本地 Wan 2.2（免费）或 Seedance（付费），生成后自动当作参考视频做动作捕捉（四足也可）。
- Comfy Desktop 里已有的 MiniMax H3 视频模型未接入默认流程：其社区许可证对本地输出的商用另需付费授权，且可能有地区限制。
- **姿态合理性检查**（评分新项）：躯干后仰（以脚尖方向为前，p90 > 25° 扣分）和膝盖反折（膝盖落在髋-踝连线之后 > 2% 身高扣分）。之前评分只看脚和平滑度，扭曲的姿势也能得高分；现在“侧视渲染视频再做动捕”这类错误结果会从 61 降到 36 并提示原因，自动调优也不会再用缩小步幅去“刷分”。
- 生成视频实测（Wan 2.2 本地，每段 5 秒约 3 分钟）：熊 → Sample Bear 39 分（AI 生成的熊腿部颜色与身体接近，2D 关键点交叉、抖动大；已改为对生成视频固定相机俯角 0°，30→39）；人 → UE5 Manny 81→83（人物只占画面 1/6，2D 精度有限）。提示词已改为“主体占画面约 2/3 高度”。人形动作建议优先用 Describe the motion（Kimodo，90+）。
- 重定向新增“从着地脚恢复前进位移”（相机跟拍/原地动作：着地脚明显后滑而身体不动时自动启用，`recover_travel`）。

### 3.7.4 更大的姿态模型 + 本地图片→3D（TRELLIS.2）（2026-11-10）
- **ViTPose++ Huge**（usyd-community/vitpose-plus-huge，3.6 GB，已下载到 `data/models/mocap`）：动作捕捉现在自动用已安装的最准模型（顺序 huge → large → base → small，`mocap/models.PREFERRED_ORDER`）；Make animation 也可传 `options.pose_variant` 指定。
- 同一批生成视频重测（`genvideo_anim_e2e.py`，`CUTLAB_REUSE` 复用视频）：

| 用例 | base 模型 | huge 模型 | huge + 新视频（主体占画面 2/3） |
|---|---|---|---|
| 熊 → Sample Bear（动捕 / 自动调优后） | 30 / 39 | **48 / 58**（2D 抖动大幅下降） | — |
| 人 → UE5 Manny（动捕 / 自动调优后） | 81 / 83 | 80 / 84.5（抖动 2.6 → 0.6） | **84 / 90.2** |

  结论：人形的瓶颈是主体在画面中太小——新提示词 + 大模型后达到 90+；AI 生成的熊仍受限于腿部与身体颜色接近（模型看不清腿），四足动作建议用真实视频或之后的文本→动作。
- **3D Models → Generate → “TRELLIS.2 - local”**（`services/providers/trellis_local.py`）：Microsoft TRELLIS.2（MIT），ComfyUI 原生节点，图结构照搬官方模板 `3d_pixal3d_trellis2_image_to_model`（TRELLIS.2 分支）：BiRefNet 去背景 → 裁到主体 1024 px → DINOv3 条件 → 稀疏结构 → 形状（512）→ 上采样（1536，Draft 跳过）→ 贴图 → 重网格+减面+UV 展开 → 烘焙底色/金属/粗糙/AO/法线 → GLB。
  - 免费、本机 GPU、不上传；无需费用确认（`gen3d.FREE`）；没有云端 Key 时 Auto 自动选它。
  - 文字输入先用 Z-Image Turbo（本地，Apache-2.0，复用 Comfy Desktop 已有权重）生成参考图再建模。
  - 质量：Draft 10 万面/1K 贴图 **136 s**；Standard 30 万面/2K（约 7 分钟，1536 上采样每步 ~22 s）；High 70 万面/4K（未实测）。RTX 3090 Ti。
  - 输出为**未绑定骨骼**的带 PBR 贴图模型，进入 Model library；要做动画需先用 Tripo/Meshy/Mixamo 绑定。
  - 权重 ≈9 GB（Comfy-Org/TRELLIS.2 int8 5.25 GB + 形状/贴图 VAE 2.0 GB + DINOv3 1.2 GB + BiRefNet 0.44 GB），`bash scripts/install_comfyui.sh --3d`。DINOv3 原始权重为 Meta DINOv3 许可（允许商用）。
  - MCP 新工具 `generate_3d_model`（prompt 或 media_id）。测试 `backend/tests/gen3d_local_e2e.py`。
  - 实测：文字“友好的卡通机器人”→ 30 万三角面、3 张贴图的 GLB，形状与参考图一致（`docs/trellis_local_robot.png`）。

### 3.7.5 循环动画：自动选取整步周期（2026-11-11）
- 问题：`loop` 原来直接把整段结尾 6 帧（0.2 s）交叉淡入开头。只有当片段恰好是“整数个步态周期 + 6 帧”时，被混合的两组姿势才一致；否则会把相差半步的姿势混在一起，脚在地上滑（Quinn 走路：滑步 5.7% → 23%，分数 92.8 → 66.8）。
- 修复（`blender/retarget.py` `find_loop_window`）：开启循环时，在源动作里找“开头附近 k 帧与结尾附近 k 帧姿势最接近”的区间（按每帧关节重心对齐，Y-up/Z-up 都适用，误差按身高归一），优先更长（更多周期）且误差相近的区间，再多留 k 帧给交叉淡入。找不到周期（误差 > 6% 身高，如一次性动作）时保持原行为并在 notes 里说明。可用 `options.loop_window = "off"` 关闭。
- 指标修复（`metrics.py` 循环接缝）：只算身体骨骼（`body_bones`），UE5 的 `ik_foot_*`/`ik_hand_*`/`center_of_mass`/`interaction` 辅助骨停在原点、不跟随位移，之前让所有带位移的循环都显示“接缝 8-13 倍”。
- 结果（MCP `edit_animation` “make it a seamless loop”）：Quinn 走路循环 1.13 s（35 帧，一个完整步态），**94.0 分**，接缝 1.06 倍正常帧间距，滑步 5.8%，无问题；导出 `exports/Quinn_Walk_Loop/`（GLB + FBX，88 骨骼，蒙皮网格，原地）。预览 `docs/quinn_walk_loop_x4.mp4`（循环 4 次）。

### 3.7.6 一句话做循环动画更稳（2026-11-12）
- **文字生成自动循环**：`quick.is_cyclic_prompt` 识别走/跑/慢跑/潜行/待机等重复动作（中英文），且不含一次性动作词（坐下、跳、挥剑、转身、然后…）。命中时以 `loop=True, loop_window="require"` 重定向：找到完整周期才循环，找不到就保持一次性片段（`info.loop` 回传，片段的 loop 标记与之一致）。显式传 `options.loop` 时以用户为准。
- **自动调优不再违背意图**：不再改 `loop`/`in_place`（原来会“不强制循环”）；步幅最多缩到起始 scale 的 80%（`MIN_SCALE_FACTOR`，原来可连续缩到 65%）；循环接缝只用平滑处理（整周期裁剪已由重定向器完成）。
- **按 clip_id 编辑/调优**：`POST /api/anim/quick/edit` 与 `/autotune` 接受 `clipId`，自动查出源动作、角色、设置（含裁剪）和指标；MCP `edit_animation(clip_id, instruction)`、新工具 `auto_tune_animation(clip_id)`，`make_animation` 新增 `loop` 参数，结果都带 `loop`、问题列表和循环说明。
- **步态标签**：先看片段名/描述（“UE5 Walk Fwd”、Kimodo 提示词），再看测量；脚不离地不标 walk/run（挥剑带两步不算走路）；继承自源动作的 idle/walk/loop 等测量类标签会被重算覆盖。已对库里 119 个片段重算，修正 29 个（如 UE5 Walk 系列原标 idle、Jog 系列原标 walk）。
- 实测（MCP）：慢跑提示词自动循环 ✔；调优保持循环、scale 停在 0.8 ✔；按 clip_id “20% slower” → 1.47 s 循环 89.6 分 ✔。
- 已知问题：Kimodo 慢跑重定向本身质量差（不循环也只有 52 分：滑步 37%、脚陷地 8.8%），跑步类的脚锁定/接地需要单独改进。→ 已在 §3.7.7 修复。

### 3.7.7 跑步的着地判断（2026-11-12）
- **原因**：着地只看脚的高度。跑步时脚落地前几帧贴着地面以 4–7 m/s 前移，也被算作“着地”。这些帧的落脚点在身体前方很远，腿够不着，地面修正（ground_fix）就把骨盆往下压，有时一直压到脚踝高度。结果整个人下蹲，另一只脚穿进地面，还出现滑步。
- **改法**（`blender/retarget.py`）：
  - `detect_contacts` 现在返回两个结果：`contact`（只看高度，脚锁定用这个，和以前一样）和 `settled`（低且水平速度低于 clip(0.25·p90 速度, 0.4, 1.0 腿长/s)）。
  - 落脚点（`plant_xy`）和骨盆下压量只用 settled 帧计算。
  - 一段着地里如果一帧都没站稳（翻滚、滑行），就整段照旧处理。
  - 原地素材的旅行量没能恢复时，站稳的帧会太少（少于 30%），这时退回只看高度。
  - 地面修正最多把骨盆压低腿长的 20%。
  - `options.contact_gate=False` 可以恢复旧算法，用于对比。
- **试过但没采用的方案**：
  - 落地前的帧逐渐过渡到锁定：贴地移动的脚被算作滑步，分数掉到 55–64。
  - 允许未站稳的帧小幅下压骨盆（1.5%–4% 腿长）：走路略好，慢跑循环掉到 65–68。
  - 放宽速度阈值：慢跑变差。
- **A/B 结果**（Quinn，旧 → 新）：

  | 动作 | 不循环 | 循环 |
  |---|---|---|
  | Kimodo 慢跑 | 52.0 → 79.1 | 49.4 → **96.4** |
  | Kimodo 走路 | 92.8 → 90.7 | 94.0 → 95.6 |
  | 跑-跳-翻滚 | 73.3 → 79.4 | 84.5 → 84.8 |
  | walk_00 | 86.2 → 91.8 | 86.3 → 85.8 |
  | 潜行 | 93.6 → 92.1 | 85.7 → 83.7 |

  UE5 Jog/Walk 系列和视频提取的走路分数不变。
- MCP 实测：对旧慢跑循环跑 `auto_tune_animation`，第一次就得 96.4 分，没有标出问题，步幅保持 1.0，不再需要缩小。前后对比见 `docs/jog_loop_before_after.png`（上排旧版，下排新版）。
- 测试：anim_e2e 51/51 通过；anim_text_e2e SUMMARY PASS。


### 3.7.8 MotionBricks：生成过渡动作和无缝循环（2026-11-13）
- **是什么**：NVIDIA MotionBricks（SIGGRAPH 2026）的 SOMA 模型。它不是文字生成动作，而是"给定前 4 帧和要到达的 1–4 帧，生成中间 0.6–1.9 秒"。CutLab 用它做两件事：
  - **两段动作之间的过渡**：A 的结尾 → 生成的过渡 → B 的开头。
  - **一次性动作变无缝循环**：从结尾生成回到自己开头的过渡。
  只支持 SOMA 骨架（Kimodo 的输出就是），结果作为新的源片段，再用原来的重定向流程套到任何角色上。
- **许可**：
  - 模型 `mmm_v6_soma_fp16.onnx`（380 MB，SHA256 与仓库 LFS 指针一致）采用 NVIDIA Open Model Agreement，**可商用**。
  - 推理代码是官方 Apache-2.0 参考实现（Warp + TensorRT）的 numpy 移植，在 CPU 上用 onnxruntime 运行：首次加载约 8 秒，之后每次约 0.3 秒。
  - 仓库里的 Bones 样例动作是"仅评估"许可，**没有使用**。
  - 文件放在 `data/models/motionbricks/soma/`（ONNX + `soma_n1.json` + `soma_model_rig.npz` + LICENSE.md）。
- **关键坑：T-pose 约定**。Kimodo 写出的 SOMA BVH 是 "standard" T-pose，ONNX 输入要的是 "capture" T-pose 的全局旋转（图里自己再转一次）。不转换的话，每个关节差 80°–170°，生成的第一帧和上下文对不上，端点修正会硬拉，接缝处出现 14 m/s 的跳变。转换方法：`R_capture = R_standard · S_j`，输出时 `R_standard = R_capture · S_jᵀ`，`S` 取自 `tools/onnx/assets/soma_model_rig.npz` 的 `standard_offsets`。转换后第一帧旋转误差中位数 4°，接缝连续。
- **摆放**：
  - 过渡时，B 放在 A 结尾前方 `distance` 米处（默认按 A 结尾和 B 开头的平均速度 × 1.3 s 估算），朝向等于 A 最后的朝向 + `turn_deg`。
  - 循环时，B 就是 A 的开头，并保持 A **自己第 0 帧的朝向**。第一版用了 A 结尾的朝向，"走过去再转身走回来"这类动作每一圈会差 180°，得分 69.9；修复后 92.8。
  - 循环时重定向用 `loop_window="none"`，整段都保留。
- **接口**：
  - `POST /api/anim/quick/transition {clipA, clipB?, loop?, rigId?, distance?, turnDeg?, options?}`，`GET /api/anim/quick/transition/status`。
  - MCP 工具 `make_transition`。
  - 代码在 `app/services/anim/motionbricks.py`；`quick.transition()` 走和 Kimodo 一样的"源片段 → 重定向 → 三视图 → GLB"流程。
- **实测（UE5 Quinn）**：

  | 组合 | 得分 |
  |---|---|
  | 站立 → 挥手 | 100 |
  | 冲刺 → 站立 | 97.0 |
  | 下劈 → 无缝循环 | 99.7（接缝 1.12 倍于正常一帧） |
  | 走路 → 下劈 | 91.5 |
  | 走路转身 → 循环 | 92.8 |

  走路 → 下劈的视觉效果是"减速停下、站开、双手举起"，过渡自然。扣分在于脚穿地 4.3%，主要继承自原来的走路片段，原片段从第 12 帧起就有穿地。
- **测试**：`tests/motionbricks_e2e.py` SUMMARY PASS（接缝连续、过渡速度不超过源动作、骨长不变、循环、API、参数校验）；anim_e2e 51/51；anim_quick_e2e PASS。
- **界面（2026-11-13）**：Animation → Make animation 的第 3 步（预览与编辑）里，如果当前动作是用文字生成的（Kimodo，或者 MotionBricks 自己的结果），会出现 "Join or loop" 区块：
  - **Make a seamless loop**：把当前动作做成无缝循环。
  - **Then play…** 下拉框加 **Join with a transition**：选另一个用文字生成的动作接在后面，结果还可以继续接、继续做循环。
  - 实测：挥手 → 前踢 得 99.5；再把它做成循环 得 99.3。代码在 `frontend/src/modules/anim/AnimWizard.tsx`。

### 3.7.9 Unreal 动画包套到 Quinn：根运动、骨骼名、同骨架直接复制（2026-11-13）
第一轮批量把 GASP / Lyra 动画套到 UE5 Quinn 上，跑了 614 条后停下，发现四个问题，修完后删掉这些片段重跑：
1. **根运动丢失**：`blender/import_motion.py`。UE 的 FBX 把位移放在 `root` 骨骼上，Blender 导入时把它变成了骨架**对象**本身。原来只在循环外读一次 `matrix_world`，结果所有位移都丢了，走、跑动作变成原地踏步，重定向只能靠脚去猜位移。现在逐帧读取。修复后蹲走速度 2.3 m/s，踩实的脚掌只动 0.03 m/s。
2. **骨骼对应错误**：`bonemap.suggest`。Lyra 女性骨架有 249 个关节，`spine_04_latissimus_l` 这类辅助骨骼被当成脊椎，Quinn 的上半身折了约 25 cm。现在核心骨骼有六成以上同名时先按名字直接对应，并刷新了 Quinn 上旧的自动对应表。
3. **只靠关节位置重算会失真**：只用关节位置重新解算，会丢掉骨骼的扭转，跑步时脚的高度对不齐、穿地。新增**同骨架直接复制**：
   - **触发条件**：源文件是 FBX/GLB，而且角色的变形骨骼有六成以上和源文件同名时自动启用；传 `options.direct=false` 可以关闭。
   - **做法**：`retarget.py` 把源文件导入同一个 Blender 场景，逐帧读取同名骨骼的世界旋转，换算成"相对源 rest pose 的旋转"，再加到 Quinn 的 rest pose 上。朝向按 rest pose 的髋部连线对齐；源文件里没有的骨骼跟随父骨骼。
   - **骨盆高度**：直接用源骨盆高度乘以腿长比例，加上 Quinn 自己的踝高。原来按腿弯中位数估算的方法会让跑和跳陷进地里。
   - **脚锁**：只锁"真正踩实"的帧（`settled`）。按高度判断着地时会把落地减速的几帧也锁住，松开时脚会瞬移最多 50 cm。
4. **评分**：
   - bake 里新存 `settled`，评分用它判断"踩实"。只按高度判断时，干净动捕落地前的减速会被算成滑步。
   - 关掉所有清理步骤时也照样做着地检测，否则评分没有接触信息可用。
- **效果**（UE5 Quinn，同一批片段，与源动画的平均关节误差约 3–4 cm）：

  | 片段 | 修复前 | 修复后 |
  |---|---|---|
  | GASP 走 | 55–61 | 98.0 |
  | GASP 蹲走 | 55 | 99.3 |
  | GASP 跑 | 40 | 87.4 |
  | GASP 冲刺 | — | 84.3 |
  | Lyra 女性持枪慢跑 | 40–75 | 86–93 |
  | ALS 走 / 跑 | — | 100 / 95.5 |

- **已知问题**（已在 §3.7.10 修复）：从高处落下的片段着地判断不准。
- **全量重跑结果（2026-11-13）**：1716 条全部套到 UE5 Quinn 上，0 失败，保留根运动，同时有源片段和 Quinn 片段。

  | 包 | 条数 | 中位分 | ≥ 90 分 | < 60 分 | 用时 |
  |---|---|---|---|---|---|
  | GASP | 886 | 96.5 | 658 | 12 | 50 分钟 |
  | Lyra | 576 | 97.0 | 465 | 5 | 45 分钟 |
  | ALS | 254 | 100 | 201 | 3 | 29 分钟 |

  低分的几乎全是脚不在平地上的动作，属于评分方式的局限，不是动作本身有问题：
  - GASP 的 Traversal（翻越、攀上障碍物）
  - 跳跃落地
  - ALS 的落地翻滚
  - 长椅坐姿

  评分只看"脚和地面 z=0"的关系，没有考虑障碍物和座椅。

### 3.7.10 地面高度会变化的动画：攀爬、翻越、跳下悬崖（2026-11-13）
§3.7.9 的低分片段检查下来，不只是评分的问题，有两个真 bug：
1. **缩放错了**：`retarget.py` 的备用缩放用"骨盆离整段最低点多高"估算站立高度。攀爬（大部分时间在台子上）和跳下悬崖（最低点在 11 m 以下）会把它算得离谱，ALS 2 m 攀爬的缩放只有 0.5 倍，悬崖跳 0.08 倍。现在改用"骨盆离同一帧较低那只脚多高"，与地面高低无关。
2. **单位猜错**：`import_motion.py` 按第一帧所有关节的范围猜单位。GASP 攀爬片段里 `center_of_mass`、`interaction` 两个辅助骨骼被放在 16 m 外的目标点，整条动画被当成厘米，缩小了 100 倍（GASP 19 条、Lyra 1 条）。现在只用离身体不远的关节判断。

**支撑面检测**（`support_levels`，只用于动画文件 FBX/BVH/GLB/BLEND，视频估计的动作太嘈杂，不启用；`options.terrain` 可强制开关）：
- 一只脚三维速度低于 1 倍腿长/秒、持续至少 0.067 s（短跑着地只有 2–3 帧），就认为它踩在这个高度的面上。骨盆在自由落体时不算（跳到最高点时脚也会停一下）。
- 相差不到 0.2 倍腿长（约 17 cm）的面合并为一层，并贴到地面。所以踮脚、抬脚跟不算台阶。
- 另一只脚始终站在更低处时，抬起不动的那只脚算"摆姿势"，不算站在台子上。
- 原地动画（骨盆水平移动不到 0.4 倍腿长、上下不到 0.5 倍腿长）不做检测：ALS 的原地跳跃、下落循环里，脚在空中慢慢晃，会被误认成台阶。
- **地面**取"脚踩过的最低面"，而不是"脚到过的最低点"。但只有当低于它的帧全在片段开头或结尾时才这样取（悬崖跳在半空结束），否则保持原来的取法。
- 片段开头或结尾在空中（从高处落进来、跳下去没落地）的帧标为 `drop`：不判着地，评分也不当成悬空或穿地。
- **每只脚有自己的支撑面**：用它自己前后两次站立的高度。已经站到上一层才算上一层，否则算下一层。
- **影响范围**：脚锁的目标高度、地面修正、脚尖防穿地都按这只脚所在的面算。bake 新存 `terrain_lo`、`terrain_hi`、`foot_floor`。评分的穿地和着地按每只脚自己的面算，悬空按附近最高的面算。
- 评分另外把"竖直速度超过 2 m/s"也算作腾空。游戏里的下落循环常是匀速的，不满足自由落体的加速度。

**重做**：平地片段（1573 条）的结果逐位不变，只按新评分重新打分。受影响的 143 条重新套到 Quinn 上（单位错的 20 条先重新导入），0 失败。

| 类别 | 条数 | 中位分（前 → 后） |
|---|---|---|
| 单位猜错的攀爬/翻越 | 20 | 58.7 → 87.4 |
| 地面高度变化（攀爬、跳上跳下、悬崖） | 101 | 75.4 → 94.5 |
| 只受站立高度估算影响（原地跳跃等） | 22 | 65.0 → 93.8 |

例：ALS 2 m 攀爬 69.7 → 87.1，GASP 悬崖起跳 62.2 → 87.7，Lyra 下落着地 59.0 → 97.3，GASP 1 m 跑上台 60.9 → 78.3。

| 包 | 中位分 | ≥ 90 分 | < 60 分 | < 70 分 |
|---|---|---|---|---|
| GASP | 96.5 → 96.8 | 658 → 717 | 12 → 1 | 45 → 2 |
| Lyra | 97.0 → 97.2 | 465 → 471 | 5 → 3 | 22 → 16 |
| ALS | 100 → 100 | 201 → 220 | 3 → 3 | 10 → 8 |

**剩下的低分**：与地面无关，包括冲刺的膝盖方向判定、Lyra 的瞄准姿势（AO）被判"后仰"、长椅坐姿、ALS 落地翻滚。

**已知问题**：
- ~~脚锁在"着地瞬间"会拉动膝盖~~ → 已修，见 §3.7.11。
- 少数姿势库片段（几帧不同姿势放在不同高度）仍会报悬空，这是数据本身的情况。

### 3.7.11 脚锁着地 / 抬脚瞬间的膝盖弹跳（2026-11-13）
**现象**：腿部骨骼的抖动 88% 出现在着地或抬脚前后 3 帧以内。关掉脚锁后，腿部抖动 p95 从 23° 降到 7–9°，所以问题出在脚锁本身。最严重的一例是 GASP `Mantle 1 0 run F Rfoot`：第 19→20 帧，膝盖从 135° 一帧之内弹直，脚踝跳了 1 m。

**原因**（`retarget.py` 的 `foot_lock`）：
1. 进入接触段后，锁的权重直接为 1，目标是整段接触的中位落点。攀爬时脚在"接触"的头几帧还在往台子上扫，目标在 1 m 外，腿够不着，被硬拉直。
2. IK 距离用硬截断（0.9999 倍腿长）。跑步支撑腿本来就伸到 93–99%，目标稍远一点，膝盖就在一两帧内变直。
3. 弯曲方向用休息姿势推算。Quinn 的休息姿势腿几乎是直的，所以落到"身体前方"这个兜底方向，落地翻滚这类动作会弯反。
4. `detect_contacts` 的回退规则：静止帧不到 30% 就退回"只看高度"。GASP 跑步脚尖离地只有 6–9 cm，整条腿被当成一直着地，被冻在原地一秒，身体却继续往前跑。

**改动**：
- `lock_weight`（只用于同骨架直接复制）：脚源位置离落点的**水平**距离不到 0.3 倍腿长时全锁，超过 0.6 倍时不锁，中间渐变。这个渐变系数先腐蚀再平滑（±1 帧），接触本身的软边不变。原地动画（骨盆水平移动不到 1 倍腿长）用 1.5 倍腿长才放开：这类动画的脚在"接触"里本来就在扫（Lyra MM Dash 没有根运动），要靠脚锁代替根运动。从关节位置解算的动画（视频、Kimodo、其他骨架的 BVH）不加这个门限：它们的脚本身可能陷进地面或偏离落点，需要全权重的锁把它拉回来。
- `soft_reach`：IK 距离在"源动画自己的髋–踝距离"和 0.96 倍腿长两者中较大的那个之前完全准确，超出后指数式逼近腿长，不再硬截断。站直的腿保持站直，够不着的腿也不会一帧弹直。
- 弯曲方向：源动画的膝盖弯出超过 5% 腿长时，用源动画膝盖实际的方向；否则沿用休息姿势的方向。
- `detect_contacts(travels=…)`（只用于同骨架直接复制、动画文件、骨盆移动超过 1 倍腿长）：脚在整段里**一帧都不停**（静止帧不到 5%），说明是原地动画加上重建的位移（ALS、Lyra 的跑步，着地的脚以身体速度的 0.2–1.2 倍漂移），这时只看高度，由脚锁代替根运动。脚**有时会停**，说明有真正的根运动，这时一段不停的低位是脚尖擦地（GASP 跑步 6 m/s），不算着地。原来的回退规则把这种脚冻在原地一秒，身体却继续往前跑。

**效果**（离线 A/B，同样的选项和骨骼映射）：
- 44 条样本（最抖的 16 条 + 随机 28 条）：平均分 +3.7；腿部抖动 p95 13.8° → 9.4°，单条最大 31° → 27°。
- 例：攀爬 Mantle 1 0 run F Rfoot 最大 51° → 15°；ALS LandRoll 45 → 84.6（膝盖不再弯反）；Lyra Dash Backward 62.7 → 81.3；GASP Run Spin 84.6 → 92.2；Run Box 88.4 → 92.4。
- 200 条随机同骨架片段：平均 +0.25，腿部抖动 p95 4.8° → 3.4°，单条最大 14.6° → 9.0°。
- 解算路径 143 条（Kimodo、视频、Manny、熊），与改动前的代码比：Quinn +1.2、Manny −0.5、熊 +2.8。

**重做**：库里所有重定向片段（1859 条）原地重做，片段 id 不变，0 失败。旧 bake 备份在 `data/anim/_backup_lockfix_20261113/`，数据库备份为 `anim.db.bak-lockfix`。
- 解算路径的片段，新分数比原来低 1 分以上的保留原 bake（26 条，多数是熊）：这部分旧 bake 由更早版本的代码生成，用现在的代码重做会更低，与脚锁无关。
- 换了 bake 的 117 条重新渲染了预览视频。

| 包 | 条数 | 中位分 | ≥ 90 | < 70 | < 60 | 平均抖动 p95 | 升 / 降（> 2 分） |
|---|---|---|---|---|---|---|---|
| GASP | 886 | 96.8 → 97.2 | 717 → 762 | 2 → 3 | 1 → 2 | 2.15° → 1.87° | 63 / 9 |
| Lyra | 576 | 97.2 → 97.8 | 471 → 503 | 16 → 11 | 3 → 0 | 1.59° → 1.46° | 39 / 2 |
| ALS | 254 | 100 → 100 | 220 → 219 | 8 → 5 | 3 → 2 | 2.40° → 2.31° | 5 / 8 |
| 解算：Quinn（Kimodo、视频等） | 75 | 95.9 → 97.7 | 59 → 64 | 9 → 5 | 6 → 4 | 0.40° → 0.36° | 19 / 0 |
| 解算：Manny | 31 | 91.6 → 96.1 | 20 → 29 | 2 → 2 | 1 → 0 | 0.89° → 0.93° | 17 / 0 |
| 解算：熊 | 36 | 76.6 → 76.6 | 1 → 1 | 7 → 6 | 0 → 0 | 6.71° → 6.89° | 7 / 0 |

全部 1859 条：平均 +0.8 分，150 条升 2 分以上，19 条降 2 分以上。升得最多的：Lyra 长椅坐姿 47.8 → 87.8，ALS LandRoll 45 → 84.6，Lyra 跳跃下落循环 60 → 99.3，Kimodo 躺地起身 40 → 73.6。

**已知问题**（降得最多的几条）：
- ~~GASP `Climb Start 2 5 walk F` 着地判断~~ → 根因是位移推算误判，已修，见 §3.7.12。
- ~~原地转身和 GetUp 被判"躯干后仰 / 膝盖反弯"~~ → 判定和脚的朝向都已修，见 §3.7.13。
- 几条 GASP Sprint Turn 低 1–2 分（Run Arc Small 已在 §3.7.12 修回 89）。

### 3.7.12 攀爬起步：误判"原地动画"，真实根运动被替换（2026-11-13）
GASP `Climb Start 2 5 walk F`（Lfoot 96.9 → 59.4）表面上是着地判断出错，根子在 `recover_travel`：这条带真实根运动的片段（骨盆走了 4 m）被当成"原地/镜头跟踪"的动画，真实位移被换成从脚推算的 7.84 m。推算的位移前几帧太慢，于是站着的脚被拖着走 2–4 m/s；挂墙之后又以 1.94 m/s 匀速平移。

原来的判断有三个问题：
- 身体高度用整段的高度范围。2.5 m 攀爬被当成 3.5 m 高的身体，阈值整体放大。
- "身体速度"取**所有**关节的平均，而且只看首尾直线距离。UE 的辅助骨骼（`ik_*_root` 在原点不动、`center_of_mass`/`interaction` 在几米外的目标点）把平均值拉向不动。转身类片段（先后退再前进）首尾几乎重合，被当成"身体没动"。
- 往墙上爬的脚被算作"着地后在滑"。

**改动**：
- 身体高度取第一帧身体关节的范围，排除辅助骨骼和离群点。
- 身体运动看骨盆（没有骨盆时取身体关节中心）。
- 速度取 1 秒窗口内的平均位移：转身仍算在动，视频的抖动不会累加。

**结果**：
- 49 条 GASP（Crouch/Walk/Run Pivot、Circle、Arc、Climb Start）不再推算位移，全部恢复用真实根运动。
- 14 条原地动画开始推算位移（ALS Sprint、JumpWalk、JumpRun、Flail，Lyra Dash）。以前跳跃把"身体高度"撑大，没有触发。
- 重做了受影响的 131 条：117 条更新，平均 +1.7 分，37 条升 2 分以上。14 条新结果更低，保留原 bake（ALS JumpWalk：源动画的脚在跳跃时穿到地下 25 cm；熊的视频动作）。
- 例子：Climb Start Lfoot 59.4 → 94.1，Rfoot 84.6 → 91.1；Run Arc Small L/R 81 → 89；Lyra Dash Forward 65.6 → 74.4；GASP Crouch Pivot 95 → 99。
- 旧 bake 备份在 `data/anim/_backup_travelfix_20261113/`，数据库备份为 `anim.db.bak-travelfix`。12 条预览视频已重新渲染。三套动画测试通过。

### 3.7.13 原地转身和起身：脚的朝向与"膝盖反弯 / 躯干后仰"的判定（2026-11-13）
ALS CLF TurnIP R90（64.9）、TurnIP L180、GetUp Front / Back 这类动画被判"膝盖反弯、躯干后仰"。查下来是两个问题叠在一起：

**1. 评分以脚尖方向为"前"，原地转身和起身时这个参照不成立。** 把源动画原样套过去（不锁脚）也只有 40–88 分：GetUp Front 40.0，Lyra Spawn Turn180 87.6，TurnIP L180 64.6。而库里原来那几条分数更高，只是因为它们的脚被扭偏了（和源动画差 50–86°，p90 约 150°）。
- 身体的"前"改为从髋部连线算：左髋→右髋，与竖直方向叉乘。膝盖也按这个方向判断，不再看脚尖，因为跪地、踮脚时脚尖会朝后或朝下。
- 只统计"站着"的帧：骨盆比较低那只脚高出半条腿以上，并且躯干没有倒过来。翻滚、躺地、起身的过程中躯干接近水平或倒置，算出来就是"后仰 90°"，而这两项检查本来针对的是站着时姿势扭曲。
- 全库按新判定重新打分：16 条去掉了误报，都是原地转身和起身，例如 TurnIP R90 64.9 → 94.6、GetUp Back 47.7 → 88.7、TurnIP L180 78.4 → 98.4。新增 4 条：Lyra Idle Ready AO 的往后看姿势。它们的躯干在新旧判定下都接近水平（±87°），属于用来叠加到其他动画上的姿势，和其他 AO 片段一样，算已知误报。真正有问题的片段仍然会被标出来：从侧视渲染视频做的动捕 Kimodo sneak（side）膝盖 13%，被击倒、坐椅子的片段也还在。

**2. 平脚时脚的朝向算错了**（`retarget.py` 的 flat feet）。着地的脚按骨盆的朝向放平，取这段着地期间的中位数：
- 同骨架直接复制时，源骨架的骨盆轴向可能和目标骨架不同（ALS 用的是 UE4 小白人），骨盆算出来的"前"带一个固定偏差，原地转身时两只脚一直偏离身体约 135°。现在改用源动画里脚自己的朝向；解算路径的脚噪声太大，仍然用骨盆。
- 角度先展开（unwrap）再取中位数。原来朝向跨过 ±180° 时，中位数是从 [-170 … +170] 里取的，结果没有意义。骨骼朝上或朝下（躺着）的帧没有可用的朝向，用前后帧插值补上。
- 整段都算一次着地（脚从不离地 5 cm 以上，即原地蹭步转身）且转了 20° 以上时，脚跟着源动画逐帧转。普通着地仍取中位数：跑步转身时脚是绕前脚掌转的，如果绕脚踝转，脚尖会滑。
- 脚尖朝向和源动画的平均偏差：TurnIP L180 81° → 34°，GetUp Front 86° → 26°，Spawn Turn180 51° → 40°。

**重做**：
- 先用新判定给 1859 个 bake 全部重新打分（数据库备份为 `anim.db.bak-posecheck`），再全部重做：1753 条更新，106 条新结果低 1 分以上，保留原 bake。旧 bake 备份在 `_backup_footyaw_20261113/`。
- 分数基本不变（升降都没有超过 2 分的）：评分不测脚的朝向，这次的改进体现在脚和源动画方向一致。120 条预览视频已重新渲染。
