// Animation <-> project store. An animation project is a workspace: the character (rig) and motion clip being
// worked on, the open section, and the motion-capture source video + range. Rigs and clips themselves live in
// the animation library (data/anim/anim.db) and are shared by all projects; the project remembers the selection.
import { useEffect, useRef, useState } from 'react'
import { PersonStanding } from 'lucide-react'
import ProjectHome, { NewProjectForm, ProjectsButton, SaveChip } from '../../shared/projects/ProjectHome'
import { createProjectSession } from '../../shared/projects/session'
import { projectsApi, type ProjectMeta } from '../../shared/projects/api'
import type { MediaRow } from '../../types/api'
import type { AnimClip, AnimRig } from './animApi'

export interface AnimWorkspace {
  tab: string
  clipId: string
  rigId: string
  mapRig: string | null
  video: MediaRow | null
  range: { start?: number; end?: number }
}
interface Setters {
  go: (tab: string) => void
  setClipId: (v: string) => void
  setRigId: (v: string) => void
  setMapRig: (v: string | null) => void
  setVideo: (v: MediaRow | null) => void
  setRange: (v: { start?: number; end?: number }) => void
}

const SECTIONS = [
  { id: 'make', label: 'Make animation', detail: 'Video or text → animation (4 steps)' },
  { id: 'library', label: 'Library', detail: 'Browse clips & characters' },
  { id: 'rigs', label: 'Rigs', detail: 'Import · bone map' },
  { id: 'retarget', label: 'Retarget & Export', detail: 'Motion → character' },
  { id: 'video', label: 'From Video', detail: 'Motion capture' },
  { id: 'generate', label: 'Reference video', detail: 'Generate a source video' },
]

// latest workspace + names, read by the serializer
const live: { ws: AnimWorkspace | null; rigs: AnimRig[]; clips: AnimClip[] } = { ws: null, rigs: [], clips: [] }

const summary = (ws: AnimWorkspace) => {
  const rig = live.rigs.find((r) => r.id === ws.rigId)
  const clip = live.clips.find((c) => c.id === ws.clipId)
  return {
    section: ws.tab, rigId: ws.rigId || null, rigName: rig?.name ?? null, clipId: ws.clipId || null, clipName: clip?.name ?? null,
    videoName: ws.video?.name ?? null, thumb_url: rig?.thumb_url || clip?.files.png || ws.video?.thumb || null,
  }
}

export const animSession = createProjectSession({
  kind: 'anim',
  delay: 1000,
  serialize: () => live.ws ? { body: JSON.stringify({ version: 1, ...live.ws }), meta: summary(live.ws) } : null,
})

const docOf = (raw: any): AnimWorkspace => ({
  tab: typeof raw?.tab === 'string' ? raw.tab : 'make',
  clipId: typeof raw?.clipId === 'string' ? raw.clipId : '',
  rigId: typeof raw?.rigId === 'string' ? raw.rigId : '',
  mapRig: typeof raw?.mapRig === 'string' ? raw.mapRig : null,
  video: raw?.video && typeof raw.video === 'object' && raw.video.id ? raw.video : null,
  range: raw?.range && typeof raw.range === 'object' ? raw.range : {},
})

function NewAnim({ rigs, onCreate }: { rigs: AnimRig[]; onCreate: (name: string, rigId: string, tab: string) => Promise<void> }) {
  const [name, setName] = useState('')
  const [rigId, setRigId] = useState('')
  const [tab, setTab] = useState('make')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const go = async () => {
    setBusy(true); setErr(null)
    try { await onCreate(name.trim() || 'Untitled animation', rigId, tab); setName('') } catch (e: any) { setErr(e.message || String(e)) } finally { setBusy(false) }
  }
  return (
    <NewProjectForm name={name} setName={setName} presets={SECTIONS} preset={tab} setPreset={setTab} onCreate={go} busy={busy} error={err}>
      <div className="field"><label>Character (rig)</label>
        <select className="input" value={rigId} onChange={(e) => setRigId(e.target.value)}>
          <option value="">{rigs.length ? 'Choose later' : 'No characters yet — import one in Rigs'}</option>
          {rigs.map((r) => <option key={r.id} value={r.id}>{r.name} · {r.kind} · {r.bone_count} bones</option>)}
        </select>
      </div>
      <div className="faint" style={{ fontSize: 11, marginBottom: 8 }}>
        Characters and motion clips are shared by all animation projects; a project remembers what you are working on and where.
      </div>
    </NewProjectForm>
  )
}

/** Mounted once inside AnimHub's left navigation: Projects button, save status and the start screen overlay. */
export default function AnimProjects({ state, set, rigs, clips }: { state: AnimWorkspace; set: Setters; rigs: AnimRig[]; clips: AnimClip[] }) {
  const home = animSession.use((s) => s.home)
  const id = animSession.use((s) => s.id)
  const name = animSession.use((s) => s.name)
  const lastJson = useRef('')
  const ready = useRef(false)
  live.ws = state; live.rigs = rigs; live.clips = clips

  const apply = (ws: AnimWorkspace) => {
    lastJson.current = JSON.stringify(ws)
    set.go(ws.tab); set.setClipId(ws.clipId); set.setRigId(ws.rigId); set.setMapRig(ws.mapRig); set.setVideo(ws.video); set.setRange(ws.range)
  }

  // reopen the last project after a reload
  useEffect(() => {
    const cur = animSession.use.getState().id
    if (!cur) { ready.current = true; return }
    projectsApi.doc(cur).then((raw) => apply(docOf(raw)))
      .catch((e: Error) => { if (/no saved document|404|not found/i.test(e.message)) { animSession.setCurrent(null); animSession.showHome(true) } })
      .finally(() => { ready.current = true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // autosave on any workspace change
  const json = JSON.stringify(state)
  useEffect(() => {
    if (!ready.current || !id || json === lastJson.current) return
    lastJson.current = json
    animSession.schedule()
  }, [json, id])

  const open = async (p: ProjectMeta) => {
    const ws = docOf(await projectsApi.doc(p.id))
    animSession.setCurrent(p.id, p.name)
    apply(ws)
  }
  const create = async (nm: string, rigId: string, tab: string) => {
    await animSession.flush()
    const ws: AnimWorkspace = { tab, rigId, clipId: '', mapRig: rigId || null, video: null, range: {} }
    live.ws = ws
    const p = await projectsApi.create('anim', nm, { version: 1, ...ws }, summary(ws))
    animSession.setCurrent(p.id, p.name)
    apply(ws)
  }

  return (
    <>
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 8, marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div className="faint" style={{ fontSize: 11 }}>Project</div>
        <div style={{ fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>{id ? name : 'No project open'}</div>
        <div className="row" style={{ justifyContent: 'space-between' }}><ProjectsButton session={animSession} /><SaveChip session={animSession} /></div>
      </div>
      {home && (
        <ProjectHome kind="anim" title="Animation · Projects" icon={PersonStanding} session={animSession} onOpen={open}
          describe={(p) => [p.meta?.rigName, p.meta?.clipName, SECTIONS.find((s) => s.id === p.meta?.section)?.label].filter(Boolean).join(' · ')}
          newPanel={<NewAnim rigs={rigs} onCreate={create} />} />
      )}
    </>
  )
}
