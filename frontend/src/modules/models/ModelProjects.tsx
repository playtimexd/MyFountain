// 3D Models <-> project store. A model project remembers the character/model being worked on (a fur source id:
// "rig:<id>", "export:<id>" or an existing groom "groom:<id>") and the open section. The models, grooms and fur
// settings themselves live in the shared libraries (anim.db / fur store), so several projects can reuse them.
import { useEffect, useState } from 'react'
import { Box } from 'lucide-react'
import { api } from '../../api/client'
import WorkspaceProjects, { workspaceSerializer } from '../../shared/projects/WorkspaceProjects'
import { NewProjectForm } from '../../shared/projects/ProjectHome'
import { createProjectSession, type ProjectSession } from '../../shared/projects/session'

export interface ModelWorkspace { tab: string; source: string }

const SECTIONS = [
  { id: 'library', label: 'Model library', detail: 'Browse & preview' },
  { id: 'local', label: 'On this PC', detail: 'Index local assets' },
  { id: 'generate', label: 'Generate', detail: 'Text / image → 3D' },
  { id: 'fur', label: 'Fur', detail: 'Groom a character' },
]

export const modelSession: ProjectSession = createProjectSession({ kind: 'model', delay: 1000, serialize: workspaceSerializer(() => modelSession) })

interface Rig { id: string; name: string; kind: string; thumb_url: string | null }
let rigCache: Rig[] = []

const parse = (raw: any): ModelWorkspace => ({
  tab: typeof raw?.tab === 'string' ? raw.tab : 'library',
  source: typeof raw?.source === 'string' ? raw.source : '',
})
const summary = (s: ModelWorkspace) => {
  const rig = s.source.startsWith('rig:') ? rigCache.find((r) => `rig:${r.id}` === s.source) : undefined
  return { section: s.tab, source: s.source || null, character: rig?.name ?? null, thumb_url: rig?.thumb_url ?? null }
}

function NewModel({ create }: { create: (name: string, init: ModelWorkspace) => Promise<void> }) {
  const [name, setName] = useState('')
  const [tab, setTab] = useState('library')
  const [rigs, setRigs] = useState<Rig[]>(rigCache)
  const [rig, setRig] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.get<Rig[]>('/api/anim/rigs').then((r) => { rigCache = r; setRigs(r) }).catch(() => undefined) }, [])
  const go = async () => {
    setBusy(true); setErr(null)
    try { await create(name.trim() || 'Untitled model', { tab, source: rig ? `rig:${rig}` : '' }); setName('') } catch (e: any) { setErr(e.message || String(e)) } finally { setBusy(false) }
  }
  return (
    <NewProjectForm name={name} setName={setName} presets={SECTIONS} preset={tab} setPreset={setTab} onCreate={go} busy={busy} error={err}>
      <div className="field"><label>Character / model</label>
        <select className="input" value={rig} onChange={(e) => setRig(e.target.value)}>
          <option value="">{rigs.length ? 'Choose later' : 'No models yet — generate or import one'}</option>
          {rigs.map((r) => <option key={r.id} value={r.id}>{r.name} · {r.kind}</option>)}
        </select>
      </div>
      <div className="faint" style={{ fontSize: 11, marginBottom: 8 }}>Models, grooms and fur settings are shared by all projects; a project remembers which model you are working on and where.</div>
    </NewProjectForm>
  )
}

/** Mounted once in ModelsHub's left navigation. */
export default function ModelProjects({ tab, source, go, setSource }: { tab: string; source: string | undefined; go: (t: string) => void; setSource: (s: string | undefined) => void }) {
  useEffect(() => { if (!rigCache.length) api.get<Rig[]>('/api/anim/rigs').then((r) => { rigCache = r }).catch(() => undefined) }, [])
  return (
    <WorkspaceProjects<ModelWorkspace> session={modelSession} title="3D Models · Projects" icon={Box}
      state={{ tab, source: source ?? '' }} parse={parse} summary={summary}
      apply={(s) => { go(s.tab); setSource(s.source || undefined) }}
      describe={(p) => [p.meta?.character, SECTIONS.find((x) => x.id === p.meta?.section)?.label].filter(Boolean).join(' · ')}
      newPanel={(create) => <NewModel create={create} />} />
  )
}
