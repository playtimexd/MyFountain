// Image Editor <-> project store. Every open document tab is a project: new documents, opened images and
// pasted images get a project automatically; edits are autosaved (debounced) and the tab list survives reloads
// (the IndexedDB session restore remembers which project each tab belongs to).
import { useEffect, useState } from 'react'
import { FolderOpen, Image as ImageIcon } from 'lucide-react'
import ProjectHome, { NewProjectForm, type Preset } from '../../shared/projects/ProjectHome'
import { createProjectSession } from '../../shared/projects/session'
import { canvasThumb, projectsApi, type ProjectMeta } from '../../shared/projects/api'
import type { Doc } from './doc'
import { hooks } from './doc'
import { deserializeDoc, serializeDoc, type ProjectJSON } from './io'
import { newDocument } from './ops'
import { addDoc, closeDoc, curDoc, setCur, useUI } from './store'
import { docPid, docSavedRev } from './projectIds'

const metaOf = (d: Doc) => ({ width: d.w, height: d.h, layers: d.layers.filter((l) => l.type !== 'group').length })
const thumbOf = async (d: Doc) => { try { return await canvasThumb(d.flatten(undefined, Math.min(1, 480 / Math.max(d.w, d.h))), 360, '#ffffff') } catch { return null } }

async function writeDoc(d: Doc, pid: string) {
  const rev = d.revision
  const body = JSON.stringify(await serializeDoc(d))
  const meta = await projectsApi.saveDoc(pid, body, { name: d.name, meta: metaOf(d) })
  docSavedRev.set(d, rev)
  if (d.revision === rev) { d.savedRevision = rev; hooks.ui() } // the tab's "•" now means "not yet autosaved"
  return meta
}

export const imageSession = createProjectSession({
  kind: 'image',
  delay: 2500,
  serialize: async () => {
    const d = curDoc()
    const pid = d && docPid.get(d)
    if (!d || !pid) return null
    const rev = d.revision
    const body = JSON.stringify(await serializeDoc(d))
    docSavedRev.set(d, rev)
    queueMicrotask(() => { if (d.revision === rev) { d.savedRevision = rev; hooks.ui() } })
    return { body, name: d.name, meta: metaOf(d) }
  },
  thumbnail: async () => { const d = curDoc(); return d ? thumbOf(d) : null },
})

const creating = new WeakMap<Doc, Promise<string | null>>()
const failedAt = new WeakMap<Doc, number>()
/** Create the doc's project once (concurrent callers share the same request; a failure is retried after 10 s). */
function ensureProject(d: Doc): Promise<string | null> {
  const have = docPid.get(d)
  if (have) return Promise.resolve(have)
  const pending = creating.get(d)
  if (pending) return pending
  const p = projectsApi.create('image', d.name || 'Untitled', null, metaOf(d))
    .then((m) => { docPid.set(d, m.id); docSavedRev.set(d, -1); return m.id })
    .catch(() => { failedAt.set(d, Date.now()); return null })
    .finally(() => creating.delete(d))
  creating.set(d, p)
  return p
}

/** Keep the session's "current project" = the active tab's project, and autosave changed documents. */
let lastCount = -1, lastCur = ''
function tick() {
  const { docs, cur } = useUI.getState()
  const s = imageSession.use.getState()
  // home screen: show when the last tab closes, hide when a tab appears or the active tab changes
  if (lastCount !== -1) {
    if (docs.length === 0 && lastCount > 0) imageSession.showHome(true)
    else if (docs.length > lastCount || (cur !== lastCur && docs.length)) imageSession.showHome(false)
  }
  lastCount = docs.length; lastCur = cur
  for (const d of docs) {
    const pid = docPid.get(d)
    if (!pid) {
      // start creating its project (never chain tick() on an already-pending/failed attempt: that would spin)
      if (!creating.has(d) && Date.now() - (failedAt.get(d) ?? 0) > 10000) void ensureProject(d).then((id) => { if (id) tick() })
      continue
    }
    const dirty = docSavedRev.get(d) !== d.revision
    if (d.id === cur) {
      if (s.id !== pid) { const h = imageSession.use.getState().home; imageSession.setCurrent(pid, d.name); imageSession.use.setState({ home: h }) }
      if (dirty && imageSession.use.getState().status !== 'pending' && imageSession.use.getState().status !== 'saving') imageSession.schedule()
    } else if (dirty && !saving.has(d)) {
      saving.add(d)  // a background tab changed (e.g. right before switching): save it directly
      void writeDoc(d, pid).catch(() => undefined).finally(() => saving.delete(d))
    }
  }
  if (!docs.length && s.id) { const h = s.home; imageSession.setCurrent(null); imageSession.use.setState({ home: h || true }) }
}
const saving = new WeakSet<Doc>()

export async function openImageProject(p: ProjectMeta) {
  const open = useUI.getState().docs.find((d) => docPid.get(d) === p.id)
  if (open) { setCur(open.id); return }
  const raw = await projectsApi.doc<ProjectJSON>(p.id)
  const d = await deserializeDoc(raw)
  d.name = p.name
  docPid.set(d, p.id)
  docSavedRev.set(d, d.revision)
  addDoc(d)
}

const SIZES: (Preset & { w: number; h: number })[] = [
  { id: '1080p', label: 'Full HD', detail: '1920×1080', w: 1920, h: 1080 },
  { id: '4k', label: '4K UHD', detail: '3840×2160', w: 3840, h: 2160 },
  { id: 'square', label: 'Square post', detail: '1080×1080', w: 1080, h: 1080 },
  { id: 'story', label: 'Story 9:16', detail: '1080×1920', w: 1080, h: 1920 },
  { id: 'portrait', label: 'Portrait 4:5', detail: '1080×1350', w: 1080, h: 1350 },
  { id: 'thumb', label: 'Video thumbnail', detail: '1280×720', w: 1280, h: 720 },
  { id: 'a4', label: 'A4 print', detail: '2480×3508 · 300 dpi', w: 2480, h: 3508 },
  { id: 'custom', label: 'Custom', detail: 'Set width × height', w: 0, h: 0 },
]

function NewImage() {
  const [name, setName] = useState('')
  const [preset, setPreset] = useState('1080p')
  const [w, setW] = useState(1600), [h, setH] = useState(1200)
  const [bg, setBg] = useState<'white' | 'transparent' | 'black'>('white')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const r = SIZES.find((x) => x.id === preset)!
  const W = r.id === 'custom' ? w : r.w, H = r.id === 'custom' ? h : r.h
  const go = async () => {
    setBusy(true); setErr(null)
    try {
      const d = newDocument(W, H, bg === 'black' ? '#000000' : bg, name.trim() || 'Untitled')
      if (!(await ensureProject(d))) setErr('The document was created, but the engine is offline — it will be saved as a project once the engine is back.')
      tick(); imageSession.showHome(false); setName('')
    } catch (e: any) { setErr(e.message || String(e)) } finally { setBusy(false) }
  }
  return (
    <NewProjectForm name={name} setName={setName} presets={SIZES} preset={preset} setPreset={setPreset} onCreate={go} busy={busy} error={err}>
      {r.id === 'custom' && (
        <div className="row" style={{ marginBottom: 10 }}>
          <div className="field grow" style={{ margin: 0 }}><label>Width px</label><input className="input" type="number" min={1} max={8192} value={w} onChange={(e) => setW(Math.max(1, Math.min(8192, +e.target.value || 1)))} /></div>
          <div className="field grow" style={{ margin: 0 }}><label>Height px</label><input className="input" type="number" min={1} max={8192} value={h} onChange={(e) => setH(Math.max(1, Math.min(8192, +e.target.value || 1)))} /></div>
        </div>
      )}
      <div className="field"><label>Background</label>
        <div className="seg-tabs">{(['white', 'transparent', 'black'] as const).map((b) => <button type="button" key={b} className={bg === b ? 'on' : ''} onClick={() => setBg(b)}>{b[0].toUpperCase() + b.slice(1)}</button>)}</div>
      </div>
      <div className="faint" style={{ fontSize: 11, marginBottom: 8 }}>Canvas {W}×{H} px.</div>
      <button type="button" className="btn" style={{ width: '100%', justifyContent: 'center', marginBottom: 8 }} onClick={() => window.dispatchEvent(new Event('ie-open-file'))}>
        <FolderOpen size={14} />Open an image file as a new project…
      </button>
    </NewProjectForm>
  )
}

const describe = (p: ProjectMeta) => {
  const m = p.meta || {}
  return [m.width && m.height ? `${m.width}×${m.height}` : '', typeof m.layers === 'number' ? `${m.layers} layer${m.layers === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')
}

export default function ImageProjects() {
  const home = imageSession.use((s) => s.home)
  const [key, setKey] = useState(0)
  useEffect(() => {
    tick()
    const t = setInterval(tick, 1000)
    const un = useUI.subscribe((s, p) => { if (s.docs !== p.docs || s.cur !== p.cur) tick() })
    return () => { clearInterval(t); un() }
  }, [])
  useEffect(() => { if (home) setKey((k) => k + 1) }, [home])
  if (!home) return null
  return (
    <ProjectHome kind="image" title="Image Editor · Projects" icon={ImageIcon} session={imageSession} onOpen={openImageProject} describe={describe}
      newPanel={<NewImage />} refreshKey={key}
      onDeleted={(id) => { const d = useUI.getState().docs.find((x) => docPid.get(x) === id); if (d) { docPid.delete(d); closeDoc(d.id) } }}
      onRenamed={(id, name) => { const d = useUI.getState().docs.find((x) => docPid.get(x) === id); if (d) { d.name = name; hooks.ui() } }} />
  )
}
