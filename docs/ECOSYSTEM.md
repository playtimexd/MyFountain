# CutLab × my-engine：游戏原型闭环生态

> 核心判断（你的观点）：代码交给 AI vibe coding；**美术资产（角色、动画、音效、音乐、场景、过场）才是把原型做"高级"的关键**。所以 CutLab 定位为**资产工厂 + 大型资产库**，`D:\THreejsEngine\my-engine` 定位为**运行时 + 关卡编辑器**。两边都有 MCP，可由同一个 Claude Code / Codex 会话驱动（用你的订阅额度，不需要在软件里填 API Key）。

## 1. 闭环

```
         ┌──────────────────────────── CutLab（资产工厂） ────────────────────────────┐
 获取 ── │ Asset Hub：Poly Haven / ambientCG / Kenney / Quaternius / Poly Pizza /       │
         │   Freesound / Sketchfab / Google Fonts / 动作库  —— 全部带许可证与署名        │
 生成 ── │ 角色：Meshy / Tripo（API Key）  图像：图片编辑器+AI  音乐：ACE-Step           │
         │ 配音：TTS  参考视频：Seedance / 本地视频模型  音效：（规划）                    │
 加工 ── │ 视频→动作（mocap）→ Blender 重定向 → 动画资产库；超分/抠图/去水印/修复        │
         └───────────────┬─────────────────────────────────────────────▲─────────────┘
                         │ GLB(网格+骨骼+全部动作) / PNG / MP3 + 许可证      │ 试玩录像 + 事件标记
                         ▼  POST :4173/api/assets 或 my-engine MCP          │（Playwright 逐帧录制）
         ┌──────────────────────────── my-engine（运行时） ──────────────────┴─────────┐
         │ 资源库 :4173 → 场景编辑器 :4174 → 10 个模板 → 试玩 → 构建                       │
         │ 代码：threejs-game-skills（本机已装）+ ENGINE_GUIDE + vibe coding               │
         └──────────────────────────────────────────────────────────────────────────────┘
                         ▲
              Claude Code / Codex：同时连 cutlab MCP 与 my-engine MCP
```
预告片 / 过场动画：试玩录像回到 CutLab 视频编辑器，配 AI 音乐、配音、字幕后导出。

## 2. 本地三个 Three.js 项目的结论（调研见会话记录）

| 项目 | 定位 | 用途 |
|---|---|---|
| **my-engine** | 唯一真正的引擎：资源库 HTTP+MCP、场景编辑器、场景 JSON、10 个模板 | **作为运行时** |
| yeshou/game（夜收） | 大型单机游戏，**已实现蒙皮 GLB 动画**（`src/view/assets.ts`、`characters.ts`、`gait.ts`）和**确定性录像**（`scripts/film-capture.mjs`） | 把这两块移植进 my-engine |
| VoidBreach / Hatchlands | 单个游戏；Hatchlands 用 FBX + Mixamo 片段 | 参考 |

## 3. my-engine 需要补的（另一个仓库，单独会话处理）
1. `Assets.instantiate` 保留 `gltf.animations`；新增 `libraryClips(id)`。
2. `AnimationStateMachine` / `CharacterAnimator` 支持场景 JSON 配置：`clipSource`、`clipMap`（idle/walk/run/jump…）、一次性动作、速度联动；移植 yeshou 的 `prepareClips` / `gaitPhase`。
3. 资源元数据：可选 `rig`/`clips` 字段（导入 GLB 时解析），导入接口支持**稳定 id**（CutLab 需要）。
4. 录像：移植 yeshou 的 Playwright 逐帧录制 → mp4 → 自动导入 CutLab（`POST /api/media/upload`），附 `events.jsonl` 作为时间线标记。
5. 资源库预览里可选播放动作片段。

## 4. 资产来源与许可策略
默认只收 **CC0 + CC-BY（自动记录署名）**；NC/ND 需手动开启。每个资产存：来源、作者、许可证、署名文本、是否可商用。导出游戏时一键生成 `CREDITS.md`。
* 免 Key、可自动获取：**Poly Haven**（CC0，官方 API，要求独特 User-Agent 并**在作品中署名 Poly Haven**）、**ambientCG**（CC0，API v3，个人运营，量要小）、Google Fonts（OFL）、**CMU 动捕**（"free for all uses"，不可直接转售；FAQ 要求不要爬取 → 逐个获取，每次上限 40）。【已查证】
* 只能手动导入（目录 + 你下载 zip 后导入）：**Kenney**（CC0，但 robots.txt 禁止机器人下载）、**Quaternius**（**2026-08-28 起不再是 CC0**，改为 Quaternius Asset License 1.0：可商用、不可作为资源包再分发）、Mixamo（无 API，原始文件不可再分发）、Truebones（付费为主）。【已查证】
* 需 Key：**Freesound**（**免费 API 仅限非商用**；只下高质量预览，原文件需 OAuth；60 次/分钟、2000 次/天）、Poly Pizza（文档页 404，接口未实测）、Sketchfab（下载需你的 token；无 token 时搜索被其防机器人机制拦截，我们不绕过）。
* 只用官方 API / 官方下载链接，遵守各站条款与限流，不绕过登录。

### 4.1 资源库扩充（2026-11-12）
| 批次 | 来源 / 许可证 | 入库方式 | 数量 |
|---|---|---|---|
| 100STYLE 动捕 | Zenodo record 8127870（`100STYLE.zip`，MD5 校验），**CC BY 4.0**，作者 Ian Mason / Sebastian Starke / Taku Komura | 按 `Frame_Cuts.csv` 裁掉每条录制首尾的无效段，改名为 `<风格>_<动作>.bvh`（forward/backward/sideways walk/run、idle、gait transitions），`/api/hub/import` 导入；描述来自 `Dataset_List.csv`，然后重新生成文本向量 | 810 条（100 种风格），60 fps，23 关节 |
| game-icons.net 图标 | GitHub `game-icons/icons`，**CC BY 3.0**（Viscious Speed、Zeromancer 为 CC0），按作者分包导入，署名写 "Icons made by {author}" | SVG → 512px 白色透明 PNG（fontTools 解析路径 + matplotlib 填充，保留镂空），SVG 原件放在 PNG 旁；kind = `sprite`；`badges/`、`various-artists/` 两个目录作者不明，未导入 | 4178 个，35 个作者包，约 160 MB（同时登记进了媒体库） |
| Poly Haven 模型 | 官方 API，CC0，1k 贴图 | `/api/hub/add` 逐批添加，**只要 ≤15 万三角面**的模型（树木类 100 万–700 万面、单个 50–490 MB，不适合游戏，已剔除） | 150 个，约 323 MB |
| Kimodo 常用动作 | 本地生成（Kimodo-SOMA，NVIDIA Open Model License），套在 UE5 Quinn 上 | `/api/anim/quick/generate-text`，得分 < 85 自动调参 | 30 个，在 Animation 库 |

扩充后：5445 个资产，4.06 GB（动画 851、模型 183、2D 图标 4178）。

**Kimodo 30 个动作的得分**：19 个 ≥ 90；Crouch walk 88、Turn 180 89、Dance 84、Hit reaction 86、Running jump 87、Climb ladder 80、Walk backward 72；**低于 70 的**：Strafe left 62、Sit down 57、Get up 40、Death fall 25。躺倒、起身、坐下这类身体接触地面/椅子的动作，现有指标只看脚，会被误罚；侧移/后退的脚滑是真问题。

**100STYLE 得分偏低（60–65）→ 已查明是评分指标的问题（2026-11-12 修复）。** 重定向本身没问题：脚的朝向跟随源数据，接触帧里脚被锁住（速度 0.0）。问题出在 `metrics.py` 的滑步计算：
1. UE5 骨架的 foot 骨骼 tail 不在脚掌（FBX 导入时 tail 随意放，约在脚踝高度）。指标用 min(head, tail) 判断"着地"，真实动捕抬脚只有 2–4 cm，于是整个摆动期都被当成"着地在滑"。
2. 滑步速度取"head/tail 中较低那一端"的位置，两端高低交换时位置一帧跳 15 cm，算出 6–10 m/s 的假尖峰。
3. 着地首尾各忽略 2 帧，按帧数算，60 fps 时只剩 0.033 s。

修复：着地 = 足够低 **且** 重定向保存的源接触（也就是 foot lock 锁住的帧）为真；速度取 head、tail 两端中较慢的一端；首尾忽略改为约 0.067 s。全库 120 个 bake 做 A/B：92 个分数上升，2 个下降（sprint 92.7→90.5、修复后的 jog 96.4→94.7），没有一个被拉低到不合格。存库的 120 个分数已按新指标重算。
新分数：100STYLE 中性走 95.9、中性跑 96.4、僵尸走 95.1、骄傲走 92.2、老人走 92.3、醉汉走 71.0（膝盖反折 + 脚穿地 7.7%，是真问题）。Kimodo 侧移 / 后退也从 60–72 升到 95–99。
**局限**：开着 foot lock 时，着地帧几乎总是静止的，所以这个指标现在只反映锁不住的情况；脚锁上 / 松开那一下的"弹跳"不计入，以后需要单独的指标。
测试：anim_e2e 51/51；anim_quick_e2e PASS（"慢 50%"一项改为循环片段比较步频：一个循环只保留一个完整周期，慢下来不一定更长）。

### 4.2 MegaSample（用户提供的 Google Drive 文件，2026-11-13）
三个 zip（ProjectMegaSample 940 MB、MegaSample_Lite 801 MB、SuperLiteSample 767 MB）都是 UE 5.5 工程，原件在 `data/hub/incoming/gdrive/`。Lite 是 ProjectMegaSample 的子集，SuperLite 有 160 个独有文件（ALS 动作、脚步声）。合并后用 UE 5.9 后台导出（`data/hub/tmp/ue_megasample/`：`Export.uproject` + `export_all.py` / `export_skm_glb.py`）：动画、骨骼网格导成 FBX，骨骼网格另导一份带材质的 GLB 作为主文件，静态网格导成 GLB，声音导成 WAV。共 2495 + 18 个文件，0 失败。
入库：GASP 887、Lyra 576、ALS 254、武器动画 36、小白人姿势 39；武器 14 把 + 小白人 4 个（GLB 带贴图）；静态网格 28；音效 544（另有 113 个与已有文件字节相同，已去重）。许可证统一为 `custom:Unreal Engine EULA`（restricted，只能用在 Unreal 项目里）；ALS 作者标为 LongmireLocomotion。
导入后资源库：7827 个资产，7.67 GB。

脚本留在会话 scratchpad（`stage_100style.py`、`svg2png.py`、`import_icons.py`、`ph_models.py`、`kimodo_batch.py`），下载原件在 `data/hub/incoming/`。

## 5. 现在的进度
| 模块 | 状态 |
|---|---|
| 视频剪辑 / 图片编辑 / 素材库 / 下载 / AI 工具 | 已完成，英文化进行中 |
| MCP 服务器（21 个工具）+ 实时桥接 | 服务器已验证；编辑器端桥接客户端开发中 |
| 动画：资产库 + Blender 重定向/导出 | 开发中（以 my-engine 的 GLB 规格为导出目标） |
| 动画：视频→动作（人形 / 四足拟合） | 开发中 |
| 生成：Seedance / 本地视频 / Meshy / Tripo | 开发中（只做 mock 测试，不花你的额度） |
| Asset Hub（联网资产库） | 开发中 |
| ACE-Step 本地音乐模型 | 首次启动正在下载权重 |
| 文字转语音 | 开发中 |
| my-engine 改造 | 待你确认（另开会话） |
