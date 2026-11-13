// Video Editor <-> project store: autosave of the timeline, the Projects start screen, migration of the
// pre-project localStorage timeline. The editor store (./store) keeps its own localStorage cache, which is
// always the document of the current project id, so a reload reopens exactly where the user left off.
import { useEffect, useState } from 'react'
import { Clapperboard } from 'lucide-react'
import ProjectHome, { NewProjectForm, type Preset } from '../../shared/projects/ProjectHome'
import { createProjectSession } from '../../shared/projects/session'
import { canvasThumb, projectsApi, type ProjectMeta } from '../../shared/projects/api'
import { projectDuration, type Project } from '../../types/project'
import { useProject } from './store'
import { newProject, normalizeProject } from './ops'
import { player } from './engine/player'
import { clock } from './engine/clock'

const VIEW_KEY = 'cutlab.video.view'
const clipCount = (p: Project) => p.tracks.reduce((n, t) => n + t.clips.length, 0)
const viewState = () => ({ playhead: Math.round(clock.time * 1000) / 1000, zoom: useProject.getState().zoom })

export const videoSession = createProjectSession({
  kind: 'video',
  serialize: () => {
    const p = useProject.getState().project
    return {
      body: JSON.stringify(p), name: p.name,
      meta: { width: p.width, height: p.height, fps: p.fps, duration: Math.round(projectDuration(p) * 100) / 100, clips: clipCount(p), tracks: p.tracks.length, view: viewState() },
    }
  },
  thumbnail: async () => {
    const cv = document.querySelector<HTMLCanvasElement>('.ve-canvas-box canvas')
    return cv && cv.width > 2 ? canvasThumb(cv) : null
  },
})

let loading = false
useProject.subscribe((s, prev) => { if (s.project !== prev.project && !loading) videoSession.schedule() })

const rememberView = () => {
  const id = videoSession.use.getState().id
  if (id) try { localStorage.setItem(VIEW_KEY, JSON.stringify({ id, ...viewState() })) } catch { /* ignore */ }
}
window.addEventListener('beforeunload', rememberView)
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') rememberView() })

function applyView(v: { playhead?: number; zoom?: number } | undefined) {
  if (!v) return
  if (typeof v.zoom === 'number') useProject.getState().setZoom(v.zoom)
  // the player learns the timeline duration asynchronously; seek once it is ready
  if (typeof v.playhead === 'number' && v.playhead > 0) setTimeout(() => { player.pause(); player.seek(Math.min(v.playhead!, Math.max(0, projectDuration(useProject.getState().project)))) }, 150)
}

function load(p: Project, id: string, name: string, view?: { playhead?: number; zoom?: number }) {
  loading = true
  try {
    player.pause(); player.seek(0)
    videoSession.setCurrent(id, name)
    useProject.getState().setProject({ ...p, name })
  } finally { loading = false }
  applyView(view)
}

export async function openVideoProject(meta: ProjectMeta) {
  const raw = await projectsApi.doc(meta.id)
  const p = normalizeProject(raw)
  if (!p) throw new Error('the saved document is not a video project')
  load(p, meta.id, meta.name, meta.meta?.view)
}

export async function createVideoProject(name: string, w: number, h: number, fps: number, from?: Project) {
  const p: Project = from ? { ...from } : newProject()
  p.name = name || 'Untitled project'
  if (!from) { p.width = w; p.height = h; p.fps = fps }
  await videoSession.flush(true)
  const meta = await projectsApi.create('video', p.name, p, { width: p.width, height: p.height, fps: p.fps, duration: projectDuration(p), clips: clipCount(p), tracks: p.tracks.length })
  load(p, meta.id, meta.name)
  return meta
}

/** First run of the project-aware editor: keep the timeline that lived only in localStorage. */
let booted = false
async function boot() {
  if (booted) return
  booted = true
  const s = videoSession.use.getState()
  const local = useProject.getState().project
  try {
    if (s.id) {
      const meta = await projectsApi.get(s.id).catch((e: Error) => (/not found/i.test(e.message) ? null : Promise.reject(e)))
      if (!meta) { // deleted from another window: keep the work as a new project
        if (clipCount(local)) await createVideoProject(local.name, local.width, local.height, local.fps, local)
        else { videoSession.setCurrent(null); videoSession.showHome(true) }
        return
      }
      let view = meta.meta?.view
      try { const v = JSON.parse(localStorage.getItem(VIEW_KEY) || 'null'); if (v?.id === s.id) view = v } catch { /* ignore */ }
      videoSession.use.setState({ name: meta.name })
      applyView(view)
    } else if (clipCount(local)) {
      await createVideoProject(local.name === 'Untitled project' ? 'Recovered project' : local.name, local.width, local.height, local.fps, local)
    }
  } catch { /* engine offline: keep editing locally; autosave reports the error */ }
}

const RES: (Preset & { w: number; h: number })[] = [
  { id: '1080p', label: 'Landscape 16:9', detail: '1920×1080 · YouTube', w: 1920, h: 1080 },
  { id: 'vertical', label: 'Vertical 9:16', detail: '1080×1920 · Shorts / TikTok', w: 1080, h: 1920 },
  { id: 'square', label: 'Square 1:1', detail: '1080×1080', w: 1080, h: 1080 },
  { id: '4x5', label: 'Portrait 4:5', detail: '1080×1350 · feed', w: 1080, h: 1350 },
  { id: '4k', label: '4K UHD', detail: '3840×2160', w: 3840, h: 2160 },
  { id: '720p', label: 'HD 720p', detail: '1280×720 · light', w: 1280, h: 720 },
  { id: 'cine', label: 'Cinema 21:9', detail: '2560×1080', w: 2560, h: 1080 },
  { id: '4x3', label: 'Classic 4:3', detail: '1440×1080', w: 1440, h: 1080 },
]

function NewVideo() {
  const [name, setName] = useState('')
  const [preset, setPreset] = useState('1080p')
  const [fps, setFps] = useState(30)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const r = RES.find((x) => x.id === preset)!
  const go = async () => {
    setBusy(true); setErr(null)
    try { await createVideoProject(name.trim() || 'Untitled project', r.w, r.h, fps); setName('') } catch (e: any) { setErr(e.message || String(e)) } finally { setBusy(false) }
  }
  return (
    <NewProjectForm name={name} setName={setName} presets={RES} preset={preset} setPreset={setPreset} onCreate={go} busy={busy} error={err}>
      <div className="field"><label>Frame rate</label>
        <div className="seg-tabs">{[24, 25, 30, 50, 60].map((f) => <button type="button" key={f} className={fps === f ? 'on' : ''} onClick={() => setFps(f)}>{f} fps</button>)}</div>
      </div>
      <div className="faint" style={{ fontSize: 11, marginBottom: 8 }}>Canvas {r.w}×{r.h} at {fps} fps. You can change both later from the top bar.</div>
    </NewProjectForm>
  )
}

const describe = (p: ProjectMeta) => {
  const m = p.meta || {}
  const d = typeof m.duration === 'number' ? `${Math.floor(m.duration / 60)}:${String(Math.floor(m.duration % 60)).padStart(2, '0')}` : ''
  return [m.width && m.height ? `${m.width}×${m.height}` : '', m.fps ? `${m.fps} fps` : '', d, typeof m.clips === 'number' ? `${m.clips} clips` : ''].filter(Boolean).join(' · ')
}

export default function VideoProjects() {
  const home = videoSession.use((s) => s.home)
  useEffect(() => { void boot() }, [])
  if (!home) return null
  return (
    <ProjectHome kind="video" title="Video Editor · Projects" icon={Clapperboard} session={videoSession} onOpen={openVideoProject} describe={describe}
      newPanel={<NewVideo />}
      onRenamed={(id, name) => { if (id === videoSession.use.getState().id) useProject.getState().mutate((p) => ({ ...p, name }), 'name') }} />
  )
}
