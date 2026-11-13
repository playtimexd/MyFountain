import { useCallback, useEffect, useMemo, useState } from 'react'
import { Copy, Gamepad2, RefreshCw, X } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'

/** Export one asset into a game-engine project (Godot / Unity / Unreal / three.js / plain folder). Backend: /api/engine/*. */
export type EngineAsset =
  | { type: 'character'; rig_id: string; clip_ids?: string[] }
  | { type: 'fur'; groom_id: string }
  | { type: 'model'; ref: string }
  | { type: 'media'; media_id: string }

interface Project { engine: string; path: string; name: string; version: string }
interface Result { engine: string; folder: string; name: string; files: string[]; unreal_script: string | null; unreal_content_path: string | null; notes: string[] }

const ENGINE_LABEL: Record<string, string> = { godot: 'Godot', unity: 'Unity', unreal: 'Unreal Engine', threejs: 'three.js', folder: 'Folder' }
const ENGINE_FORMAT: Record<string, string> = {
  godot: 'GLB → assets/cutlab/ (imports automatically)',
  unity: 'FBX → Assets/CutLab/ (imports automatically)',
  unreal: 'FBX + import script → SourceArt/CutLab/ → /Game/CutLab/',
  threejs: 'GLB → public/assets/cutlab/ (+ shellFur.ts for fur)',
  folder: 'GLB + FBX into any folder',
}
const pref = (k: string) => { try { return localStorage.getItem(k) || '' } catch { return '' } }
const store = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

export default function EngineExport({ asset, defaultName, onClose }: { asset: EngineAsset; defaultName: string; onClose: () => void }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [path, setPath] = useState(() => pref('cutlab.engine.project'))
  const [engine, setEngine] = useState(() => pref('cutlab.engine.engine') || 'godot')
  const [name, setName] = useState(defaultName.replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48) || 'Asset')
  const [overwrite, setOverwrite] = useState(false)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState('')
  const job = useJob<Result>()

  const load = useCallback(async (refresh = false) => {
    setLoading(true)
    try { setProjects(await api.get<Project[]>(`/api/engine/projects${refresh ? '?refresh=true' : ''}`)); setErr('') } catch (e: any) { setErr(e.message) } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => { const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])

  const grouped = useMemo(() => {
    const g: Record<string, Project[]> = {}
    for (const p of projects) (g[p.engine] ||= []).push(p)
    return g
  }, [projects])

  const pick = (p: Project) => { setPath(p.path); setEngine(p.engine) }
  const detect = async () => {
    setErr('')
    try { const p = await api.get<Project>(`/api/engine/detect?path=${encodeURIComponent(path)}`); setEngine(p.engine) }
    catch (e: any) { setErr(e.message); setEngine('folder') }
  }
  const run = async () => {
    store('cutlab.engine.project', path); store('cutlab.engine.engine', engine)
    try { await job.run(() => api.post('/api/engine/export', { asset, engine, project: path, name, overwrite })) } catch { /* JobBar */ }
  }
  const copy = (s: string) => { navigator.clipboard?.writeText(s).then(() => { setCopied(s); setTimeout(() => setCopied(''), 1500) }).catch(() => {}) }
  const res = job.job?.status === 'done' ? job.job.result : null

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: 'min(860px, 94vw)', maxHeight: '88vh' }}>
        <header><Gamepad2 size={16} />Export to game engine · <span className="muted">{defaultName}</span><span className="grow" />
          <button className="btn ghost icon" onClick={onClose}><X size={16} /></button></header>
        <div className="body" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, overflow: 'auto' }}>
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ marginBottom: 6 }}><b className="grow">Projects on this PC</b>
              <button className="btn ghost sm" disabled={loading} onClick={() => load(true)} title="Search D:\, Documents and Desktop again"><RefreshCw size={13} />{loading ? 'Searching…' : 'Rescan'}</button></div>
            <div style={{ maxHeight: 360, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 6 }}>
              {!projects.length && <div className="faint" style={{ padding: 12, fontSize: 12 }}>{loading ? 'Searching for Godot, Unity, Unreal and three.js projects…' : 'No engine projects found. Type a folder on the right.'}</div>}
              {Object.entries(grouped).map(([eng, ps]) => (
                <div key={eng}>
                  <div className="faint" style={{ fontSize: 11, padding: '6px 10px 2px', textTransform: 'uppercase', letterSpacing: '.04em' }}>{ENGINE_LABEL[eng] || eng} ({ps.length})</div>
                  {ps.map((p) => (
                    <div key={p.path} onClick={() => pick(p)} title={p.path}
                      style={{ padding: '4px 10px', cursor: 'pointer', fontSize: 12, background: p.path === path ? 'rgba(108,140,255,.15)' : undefined }}>
                      <div style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}{p.version ? <span className="faint"> · {p.version}</span> : null}</div>
                      <div className="faint" style={{ fontSize: 10.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.path}</div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
          <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 }}>
            <label>Project folder
              <div className="row" style={{ gap: 6, marginTop: 3 }}>
                <input className="input" value={path} onChange={(e) => setPath(e.target.value)} placeholder="D:\MyGame" style={{ flex: 1 }} />
                <button className="btn sm" disabled={!path} onClick={detect}>Detect</button>
              </div>
            </label>
            <label>Engine
              <select className="select" value={engine} onChange={(e) => setEngine(e.target.value)} style={{ width: '100%', marginTop: 3 }}>
                {Object.entries(ENGINE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <div className="faint" style={{ marginTop: 3 }}>{ENGINE_FORMAT[engine]}</div>
            </label>
            <label>Asset name (folder)
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} style={{ width: '100%', marginTop: 3 }} />
            </label>
            <label className="row" style={{ gap: 6, cursor: 'pointer' }}><input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />Replace an existing export with the same name</label>
            <button className="btn primary" disabled={!path || !name || job.busy} onClick={run}><Gamepad2 size={14} />Export to {ENGINE_LABEL[engine]}</button>
            <JobBar job={job.job} error={job.error || err || null} onCancel={job.cancel} />
            {res && (
              <div className="panel" style={{ padding: 10 }}>
                <div className="row"><b className="grow">Exported to {res.folder}</b>
                  <button className="btn ghost sm" onClick={() => copy(res.folder)} title="Copy folder path"><Copy size={12} />{copied === res.folder ? 'Copied' : ''}</button></div>
                <ul style={{ margin: '6px 0', paddingLeft: 18 }}>{res.files.map((f) => <li key={f} className="faint" style={{ wordBreak: 'break-all' }}>{f.split(/[\\/]/).pop()}</li>)}</ul>
                {res.notes.map((n) => <div key={n} style={{ marginTop: 4 }}>{n}</div>)}
                {res.unreal_script && (
                  <div style={{ marginTop: 6 }}>
                    <div className="row" style={{ gap: 6 }}><code style={{ fontSize: 11, wordBreak: 'break-all' }} className="grow">{res.unreal_script}</code>
                      <button className="btn sm" onClick={() => copy(res.unreal_script!)}><Copy size={12} />{copied === res.unreal_script ? 'Copied' : 'Copy'}</button></div>
                    <div className="faint" style={{ marginTop: 4 }}>Or ask Claude (with an Unreal MCP connected) to “run this CutLab import script in Unreal”.</div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
