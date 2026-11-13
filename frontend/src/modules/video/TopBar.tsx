import { useRef, useState } from 'react'
import { Download, FilePlus2, FolderOpen, Redo2, Save, Undo2 } from 'lucide-react'
import { useProject } from './store'
import { IconBtn } from './ui'
import { downloadProject, openProjectFile } from './actions'
import { player } from './engine/player'
import ExportDialog from './ExportDialog'
import BridgeChip from './BridgeChip'
import { ProjectsButton, SaveChip } from '../../shared/projects/ProjectHome'
import { createVideoProject, videoSession } from './VideoProjects'

const RATIOS: { id: string; label: string; w: number; h: number }[] = [
  { id: '16:9', label: '16:9', w: 1920, h: 1080 },
  { id: '9:16', label: '9:16', w: 1080, h: 1920 },
  { id: '1:1', label: '1:1', w: 1080, h: 1080 },
  { id: '4:3', label: '4:3', w: 1440, h: 1080 },
  { id: '21:9', label: '21:9', w: 2560, h: 1080 },
]

export default function TopBar() {
  const project = useProject((s) => s.project)
  const canUndo = useProject((s) => s.past.length > 0)
  const canRedo = useProject((s) => s.future.length > 0)
  const [showExport, setShowExport] = useState(false)
  const [custom, setCustom] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const st = useProject.getState

  const cur = RATIOS.find((r) => Math.abs(r.w / r.h - project.width / project.height) < 0.01)
  const selVal = custom || !cur ? 'custom' : cur.id

  const onRatio = (v: string) => {
    if (v === 'custom') { setCustom(true); return }
    setCustom(false)
    const r = RATIOS.find((x) => x.id === v)!
    // keep the long edge of the current project when possible
    const long = Math.max(project.width, project.height)
    const k = long / Math.max(r.w, r.h)
    const w = Math.round((r.w * k) / 2) * 2, h = Math.round((r.h * k) / 2) * 2
    st().setCanvas(long >= 720 ? w : r.w, long >= 720 ? h : r.h)
  }

  const open = async (f: File) => {
    const p = await openProjectFile(f)
    if (!p) { st().say('Could not read the project file'); return }
    // an opened .cutlab.json becomes a new project in the store (the current one stays as it is)
    try { await createVideoProject(p.name || f.name.replace(/\.cutlab\.json$|\.json$/i, ''), p.width, p.height, p.fps, p) }
    catch { player.pause(); player.seek(0); st().setProject(p) }
    st().say(`Opened ${p.name}`)
  }

  return (
    <div className="ve-topbar">
      <ProjectsButton session={videoSession} />
      <input className="ve-title" value={project.name} onChange={(e) => st().mutate((p) => ({ ...p, name: e.target.value }), 'name')} title="Project name" />
      <span className="ve-sep" />
      <IconBtn title="Undo (Ctrl+Z)" disabled={!canUndo} onClick={() => st().undo()}><Undo2 size={16} /></IconBtn>
      <IconBtn title="Redo (Ctrl+Shift+Z)" disabled={!canRedo} onClick={() => st().redo()}><Redo2 size={16} /></IconBtn>
      <span className="ve-sep" />
      <span className="muted">Aspect ratio</span>
      <select className="ve-select" value={selVal} onChange={(e) => onRatio(e.target.value)}>
        {RATIOS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        <option value="custom">Custom</option>
      </select>
      {(custom || !cur) && (
        <span className="row" style={{ gap: 4 }}>
          <input className="ve-num wide" type="number" value={project.width} onChange={(e) => st().setCanvas(+e.target.value || project.width, project.height)} />
          ×
          <input className="ve-num wide" type="number" value={project.height} onChange={(e) => st().setCanvas(project.width, +e.target.value || project.height)} />
        </span>
      )}
      <span className="faint" style={{ fontSize: 11 }}>{project.width}×{project.height} · {project.fps}fps</span>
      <div className="grow" />
      <SaveChip session={videoSession} />
      <BridgeChip />
      <IconBtn title="New project (the current one is saved automatically)" onClick={() => { void videoSession.flush(true); videoSession.showHome(true) }}><FilePlus2 size={16} /></IconBtn>
      <button className="btn sm" title="Import a .cutlab.json project file as a new project" onClick={() => fileRef.current?.click()}><FolderOpen size={14} />Import</button>
      <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void open(f); e.target.value = '' }} />
      <button className="btn sm" title="Download the project as a .cutlab.json file (projects are also saved automatically)" onClick={() => downloadProject(project)}><Save size={14} />Save as file</button>
      <button className="btn sm primary" onClick={() => setShowExport(true)}><Download size={14} />Export</button>
      {showExport && <ExportDialog onClose={() => setShowExport(false)} />}
    </div>
  )
}
