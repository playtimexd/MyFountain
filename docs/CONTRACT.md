# CutLab — shared contract (read this before touching anything)

Layout: `backend/` (FastAPI, Python venv at `backend/.venv`, run `backend\.venv\Scripts\python.exe -m app` → :8787),
`frontend/` (Vite + React + TS + zustand, dev server :5173 proxies `/api` → :8787), `data/` (all runtime files).
Always use **absolute paths** (cwd drifts between shell calls). Windows; PowerShell/Bash available. Project root `D:\CutLab`.
GPU: RTX 3090 Ti, but ~8 GB VRAM is usually free (other apps hold the rest) → prefer models that fit in ≤8 GB or offload.
**Don't download multi-GB model weights without asking the lead**; build adapters that detect availability
(see `/api/capabilities`) and degrade to a working fallback. pip installs go to the venv:
`uv pip install --python D:\CutLab\backend\.venv\Scripts\python.exe <pkg>`.

Ownership (never edit files you don't own; add new files freely inside your own dirs):

| Owner | Files |
|---|---|
| lead | `backend/app/{config,db,jobs,ffutil,ingest,main}.py`, routers `system, jobs, media, assets, download, segment, export`, `services/render.py`, `frontend/src/{App,api,types,shared}`, Library + Downloader modules |
| backend-ai | routers `music, ai, search, agent`, `backend/app/services/{music,upscale,inpaint,asr,rag,agent}*.py`, `frontend/src/modules/ai/**` |
| video-ui | `frontend/src/modules/video/**` |
| image-ui | `frontend/src/modules/image/**` |

## Backend conventions
* A router module in `backend/app/routers/<name>.py` exposing `router = APIRouter(prefix="/api/<name>")` is auto-registered.
* Long work → `from ..jobs import jobs; job = jobs.submit(kind, title, fn)`; `fn(ctx)` calls `ctx.progress(0..1, msg)`; return a JSON-able
  result dict. Endpoint returns `job.public()`. Poll `GET /api/jobs/{id}` → `{id,status:queued|running|done|error|cancelled,progress,message,result,error}`.
* Produce files into `config.MEDIA_DIR` then `ingest.ingest_file(path, origin=..., name=..., tags=[...])` → returns a media row (below). Job result then is `{"media": row}`.
* ffmpeg: use `ffutil.run_ffmpeg(args, total_duration=, ctx=)` (progress + cancel). `ffutil.has_nvenc()`, `ffutil.probe()`.
* Media row (JSON): `{id, kind:'video'|'audio'|'image', name, path, origin, source_url, duration, width, height, fps, size, has_audio, tags[], meta{}, created, url, thumb}`
  where `url=/api/media/{id}/file` (Range-capable) and `thumb=/api/media/{id}/thumb`.
* Settings (`config.load_settings()`): ollama_url, llm_model(qwen3:8b), embed_model(nomic-embed-text), acestep_url, whisper_model, cookies_from_browser, proxy…
* Errors: raise `HTTPException`; job errors surface in `job.error`.

## HTTP API (all JSON)
```
GET  /api/health  GET /api/capabilities  GET/PUT /api/settings {values:{}}
GET  /api/jobs  GET /api/jobs/{id}  POST /api/jobs/{id}/cancel
-- media (lead, DONE) --
POST /api/media/import {paths[],recursive,copy} -> job(result {items[],errors[]})   POST /api/media/upload (multipart file) -> row
GET  /api/media?kind=&q=&origin=&tag=   GET/PATCH/DELETE /api/media/{id}   GET /api/media/{id}/file|thumb|waveform
-- assets / materials (lead) --
GET  /api/assets/catalog  -> {transitions[{id,name}], filters[{id,name,adjust}], stickers[{id,name,url}], textPresets[{id,name,text:TextStyle}], backgrounds[{id,name,css,url}] , fonts[{family,url}]}
GET  /api/assets/fonts    -> [{family,file,url}]   (system + data/fonts)    GET /api/assets/sticker/{id}.png
GET  /api/assets/stock?q=&kind=photo|video  (optional Pexels/Pixabay if keys are set)
-- download (lead) --
POST /api/download/info {url} -> {title,thumbnail,duration,uploader,site,formats[]}
POST /api/download {url, quality:'best'|'1080'|'720'|'audio', cookies_from_browser?} -> job(result {media: row})
-- segment (lead) --
POST /api/segment {mediaId, method:'scene'|'silence'|'asr'|'fixed', threshold?, min_len?, every?, language?} -> job(result {segments:[{start,end,label,text?}]})
POST /api/segment/cut {mediaId, segments:[{start,end,label?}], reencode?:false} -> job(result {items:[media row]})   # writes each segment as its own asset
-- export (lead) --
POST /api/export {project:Project, preset:{width?,height?,fps?,format:'mp4'|'webm'|'gif'|'mp3', quality:'draft'|'normal'|'high', hwaccel:bool}} -> job(result {media: row, file})
-- music (backend-ai) --
GET  /api/music/providers -> [{id,name,available,note}]
POST /api/music/generate {prompt, duration:int(sec), instrumental:true, lyrics?, bpm?, mood?, genre?, provider?:'auto'|'acestep'|'procedural', seed?} -> job(result {media: row})
-- ai tools (backend-ai) --
POST /api/ai/upscale {mediaId, scale:2|4, model?:'auto'|'realesrgan'|'lanczos', denoise?} -> job(result {media})        # video or image
POST /api/ai/watermark {mediaId, regions:[{x,y,w,h}]  (px in source coords), method:'auto'|'delogo'|'inpaint', start?, end?} -> job(result {media})
POST /api/ai/remove-bg {mediaId | imageBase64} -> {imageBase64 (png)}                        # sync for stills; video optional (job)
POST /api/ai/inpaint {imageBase64, maskBase64 (white = fill), prompt?} -> {imageBase64}      # sync, opencv/LaMa
POST /api/ai/subtitles {mediaId, language?, translate_to?} -> job(result {segments:[{start,end,text}], srt, media?})
POST /api/ai/auto-cut {mediaId, style:'tight'|'highlights'|'talking-head', target_duration?} -> job(result {keep:[{start,end,score,reason}]})
-- search / RAG over local files (backend-ai) --
POST /api/search/index {paths[], recursive:true, kinds?:[...]} -> job   GET /api/search/status -> {indexed,roots[],last}
POST /api/search/query {q, kinds?:[...], limit:20} -> {hits:[{mediaId?|path, kind, score, snippet, start?, end?, media?:row}]}
-- agent (backend-ai) --
POST /api/agent/chat {messages:[{role,content}], project?:Project, selection?:{clipIds[],mediaIds[]}} -> {reply:string, ops:Op[], toolTrace:[...]}
```

## Project JSON (the single editing document — frontend state, export input, agent input)
See `frontend/src/types/project.ts` (authoritative TypeScript). Summary: `Project{ version:1,name,width,height,fps,tracks:Track[] }`,
`Track{id,kind:'video'|'audio'|'text',name,muted,hidden,locked,clips:Clip[]}` — **later tracks in the array render on top**.
`Clip{ id,type:'video'|'image'|'audio'|'text'|'sticker', assetId?, start, in, out, speed, volume, fadeIn, fadeOut, fit, transform{x,y,scale,rotation,opacity,flipX,flipY},
adjust{brightness,contrast,saturation,hue,blur,sharpen,vignette,sepia,grayscale,temperature}, filter?, text?:TextStyle+content, transitionIn?:{type,duration}, keyframes? }`.
Timeline duration of a clip = `(out-in)/speed`. Transform x,y = clip-centre offset from canvas centre as fraction of canvas W/H;
content is first fit (contain/cover) into the canvas, then multiplied by `scale`. Text `size` = fraction of canvas height.
Transitions apply to the first `duration` seconds of the clip over whatever is beneath (fade, slideleft/right/up/down, zoomin, wipeleft/wiperight).

## Agent Op schema (agent → frontend `applyOps`, undoable as one step)
```
{op:'add_clip',  assetId, track?:'auto'|trackId, start?, in?, out?}     {op:'split', clipId, at}
{op:'delete_clip', clipId}   {op:'trim', clipId, in?, out?}   {op:'move', clipId, start, track?}
{op:'set', clipId, patch:Partial<Clip>}   {op:'set_speed', clipId, speed}   {op:'add_transition', clipId, type, duration}
{op:'add_text', text, start, duration, style?:Partial<TextStyle>}   {op:'add_music', assetId, start?, volume?}
{op:'add_subtitles', segments:[{start,end,text}], style?}   {op:'remove_ranges', ranges:[{start,end}]}   # ripple-delete ranges on all tracks
```

## Frontend conventions
* Shared files (lead-owned, import don't edit): `src/api/client.ts` (`api.get/post`, `useJob`, `mediaUrl`, `waitJob`), `src/types/project.ts`, `src/types/api.ts`,
  `src/shared/*` (panel prop types, `Button`, `Panel`, `JobBar` …) and `src/styles/theme.css` (CSS variables; dark theme; use them).
* Each module has a default-exported root component: `modules/video/VideoEditor.tsx`, `modules/image/ImageEditor.tsx`, `modules/ai/{MusicPanel,AiToolsPanel,SearchPanel,AgentPanel}.tsx`.
  AI panels receive `PanelProps` from `src/shared/panelTypes.ts` (e.g. `onAddAsset(media)`, `project`, `selection`, `applyOps(ops)`).
* UI language: Chinese (简体) labels, English code. No external CDN; npm deps allowed (`npm install` inside `D:\CutLab\frontend`).
* Verify with `npx tsc -b` in `frontend/` (must be clean) and, for UI, drive it with the built-in browser against http://localhost:5173 (start with `npm run dev`, it may already be running).

## TypeScript gotchas in this repo (TS 7, strict-ish)
`erasableSyntaxOnly` (no `enum`, no constructor parameter properties, no namespaces), `verbatimModuleSyntax` (use `import type { … }` for types),
`noUnusedLocals/Parameters` (prefix unused params with `_`). React 19. Run `npx tsc -b` from `D:\CutLab\frontend` — it must stay clean.
Dev servers (lead keeps them running): engine http://127.0.0.1:8787 (restart needed after adding a router: lead will restart on request — or run your own copy on another port with `$env:CUTLAB_PORT=88xx`), UI http://localhost:5173.

## Animation / mocap / videogen / bridge / MCP (added later — see docs/ANIMATION.md for the full design)
Ownership: **anim-core** = `routers/{anim,videogen}.py`, `services/anim/**`, `backend/blender/*.py`, `frontend/src/modules/anim/**`.
**mocap** = `routers/mocap.py`, `services/mocap/**`. **lead** = `services/llm.py`, `routers/bridge.py`, `backend/app/mcp_server.py`, Settings dialog, App tab.
Shared: `services/llm.py` `chat(messages,*,system,tools,provider_override,...)` (ollama|anthropic; secrets never logged/returned). Blender: headless
`blender.exe -b -P <script> -- <args.json>`; exe from `capabilities.blender.exe`. Interchange file `motion.npz` and the HTTP API are specified in docs/ANIMATION.md §3.
Weights/models: never download without the lead asking the user; adapters must expose `available` + `size` + a `prepare` job.

## tts — text to speech / voice-over (backend-ai, router `tts.py`, services `tts*.py`, UI `modules/ai/TtsPanel.tsx`)
Providers: `sapi` (Windows System.Speech through Windows PowerShell; zero downloads; lists installed voices incl. zh-CN if a voice pack is installed),
`kokoro` (Kokoro-82M, Apache-2.0, via `kokoro-onnx` + `misaki-fork[zh]` already in the venv; weights are NOT present until `POST /api/tts/prepare`),
`cosyvoice` / `f5tts` (stubs: always `available:false` with a how-to note). `provider:'auto'` = kokoro when ready, else sapi. All output is mp3, media `origin:'tts'`, tags `['voice-over', <provider>]`.
```
GET  /api/tts/providers -> [{id:'sapi'|'kokoro'|'cosyvoice'|'f5tts', name, available, offline, quality, note, needs_download?, download?}]
GET  /api/tts/voices?provider=sapi|kokoro -> [{id:'sapi:Microsoft Huihui Desktop', provider, name, full_name, lang:'zh-CN', gender, available}]   (no filter = all providers)
POST /api/tts/generate {text, voice?:'<provider>:<name>'|'<name>', speed?:0.5-2, lang?:'zh'|'en', provider?:'auto'|'sapi'|'kokoro'} -> job(result {media: row, provider, voice})
POST /api/tts/batch {items:[{id, text, maxDuration?}], voice?, speed?, lang?, provider?} -> job(result {items:[{id, media: row}], provider, voice})   # <=500 items; one media per item
POST /api/tts/prepare {pack?:'zh'|'en', variant?:'int8'|'fp16'|'fp32'} -> job   # DOWNLOADS Kokoro weights into data/models/kokoro; only call after asking the user
```
* Long text is split into sentences, synthesized, concatenated (0.12 s gaps) and loudness-normalised. `speed` is applied with ffmpeg `atempo` for sapi and natively for kokoro.
* `batch`: `maxDuration` (seconds, optional) speeds a line up (at most 1.6x) so it fits its subtitle slot; read the real length from `media.duration`. Use it for per-subtitle dubbing: place each returned clip at its subtitle `start`.
* `voice` defaults per language: zh text -> a zh voice, otherwise an English voice. Errors (missing voice / model not downloaded) surface as `job.error` in English.
* Kokoro packs (`data/models/kokoro/`, from github.com/thewh1teagle/kokoro-onnx release `model-files-v1.1` + HF `hexgrad/Kokoro-82M-v1.1-zh/config.json`):
  `zh` = `kokoro-v1.1-zh.int8.onnx` 114 MB (fp16 164 MB, fp32 326 MB) + `voices-v1.1-zh.bin` 54 MB + `config.json` 3 KB -> Mandarin voices `zf_*`/`zm_*` and a few English voices;
  `en` = `kokoro-v1.0[.int8|.fp16].onnx` (92-326 MB) + `voices-v1.0.bin` 28 MB -> US/UK English (+ other languages via espeak). Output 24 kHz.
