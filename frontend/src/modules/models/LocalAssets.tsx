import { useCallback, useEffect, useMemo, useState } from 'react'
import { Gamepad2, HardDrive, PersonStanding, RefreshCw, Search } from 'lucide-react'
import { api, fmtSize, useJob } from '../../api/client'
import { emit } from '../../shared/bus'
import { JobBar } from '../../shared/ui'
import { Note } from '../anim/animUi'
import GlbViewer from '../anim/providers/GlbViewer'
import EngineExport from './EngineExport'

/** 3D / animation files indexed in place on this PC (game projects on D:\, Mixamo downloads...). */
export interface LocalAsset {
  id: string; path: string; name: string; ext: string; size: number; project: string; kind: string
  n_anims: number; clips: string[]; has_skin: boolean; has_mesh: boolean; tags: string[]; copies: number; other_paths: string[]
  info: Record<string, any>; view_url: string | null
}
interface Resp { total: number; items: LocalAsset[]; facets: { kinds: Record<string, number>; projects: { project: string; count: number }[]; mixamo: number } }

export const KIND_LABEL: Record<string, string> = {
  'animation': 'Animation', 'animated-character': 'Animated character', 'animated-model': 'Animated model', 'rigged': 'Rigged character',
  'model': 'Static model', 'scene': '.blend scene', 'unknown': 'Unknown',
}
const PAGE = 120

export default function LocalAssets({ kinds, title = 'On this PC' }: { kinds?: string[]; title?: string }) {
  const [data, setData] = useState<Resp | null>(null)
  const [items, setItems] = useState<LocalAsset[]>([])
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [project, setProject] = useState('')
  const [mixamo, setMixamo] = useState(false)
  const [sel, setSel] = useState<LocalAsset | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [eng, setEng] = useState(false)
  const idx = useJob<any>()
  const imp = useJob<any>()
  const allowed = kinds?.join(',')

  const load = useCallback(async (offset = 0) => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) })
    if (kind || allowed) p.set('kind', kind || allowed!)
    if (q) p.set('q', q)
    if (project) p.set('project', project)
    if (mixamo) p.set('tag', 'mixamo')
    try {
      const r = await api.get<Resp>(`/api/models/local?${p}`)
      setData(r); setErr('')
      setItems((cur) => (offset ? [...cur, ...r.items] : r.items))
    } catch (e: any) { setErr(e.message || String(e)) }
  }, [kind, allowed, q, project, mixamo])
  useEffect(() => { const h = setTimeout(() => load(0), 200); return () => clearTimeout(h) }, [load])

  const reindex = async () => { try { const j = await idx.run(() => api.post('/api/models/local/index', { paths: [] })); setMsg(`Indexed: ${JSON.stringify(j.result?.kinds ?? {})}`); load(0) } catch { /* JobBar */ } }
  const toAnim = async (a: LocalAsset) => {
    setMsg('')
    try {
      const j = await imp.run(() => api.post(`/api/models/local/${a.id}/to-animation`))
      const clip = j.result?.clip?.id, rig = j.result?.rig?.id
      if (clip) emit('cutlab:to-anim-clip', { clipId: clip, action: 'open' })
      else if (rig) emit('cutlab:to-anim-rig', { rigId: rig })
    } catch { /* JobBar */ }
  }

  const kindChips = useMemo(() => {
    const k = data?.facets.kinds || {}
    return Object.entries(k).filter(([id]) => !kinds || kinds.includes(id)).sort((a, b) => b[1] - a[1])
  }, [data, kinds])
  const fmt = sel?.ext === '.fbx' ? 'fbx' : 'gltf'

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(360px, 1.2fr) minmax(320px, 1fr)', gap: 12, padding: 12, height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="row" style={{ gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
          <HardDrive size={14} /><b>{title}</b><span className="faint" style={{ fontSize: 12 }}>{data ? `${data.total.toLocaleString()} files` : ''}</span>
          <span className="grow" />
          <button className="btn sm" disabled={idx.busy} onClick={reindex} title="Scan D:\, Downloads, Documents, Desktop and OneDrive for .glb .gltf .fbx .blend .bvh .dae (files stay in place)"><RefreshCw size={13} />Scan this PC</button>
        </div>
        <JobBar job={idx.job} error={idx.error} onCancel={idx.cancel} />
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', margin: '6px 0' }}>
          <div className="row" style={{ gap: 4, flex: 1, minWidth: 160, border: '1px solid var(--line)', borderRadius: 6, padding: '0 8px', background: 'var(--bg-1)' }}>
            <Search size={13} className="faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search names, paths, clip names (walk, idle, attack...)" style={{ border: 0, background: 'transparent', flex: 1, padding: '6px 0' }} />
          </div>
          <select className="select" value={project} onChange={(e) => setProject(e.target.value)} style={{ maxWidth: 220 }}>
            <option value="">All projects / folders</option>
            {data?.facets.projects.map((p) => <option key={p.project} value={p.project}>{p.project} ({p.count})</option>)}
          </select>
          <label className="row" style={{ gap: 4, fontSize: 12, cursor: 'pointer' }}><input type="checkbox" checked={mixamo} onChange={(e) => setMixamo(e.target.checked)} />Mixamo ({data?.facets.mixamo ?? 0})</label>
        </div>
        <div className="row" style={{ gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
          <button className={`btn sm ${kind === '' ? 'primary' : 'ghost'}`} onClick={() => setKind('')}>All</button>
          {kindChips.map(([id, n]) => <button key={id} className={`btn sm ${kind === id ? 'primary' : 'ghost'}`} onClick={() => setKind(id)}>{KIND_LABEL[id] || id} <span style={{ opacity: .6 }}>{n}</span></button>)}
        </div>
        {err && <Note tone="warn">{err}</Note>}
        {data && data.total === 0 && !q && !project && <Note>Nothing indexed yet. Click “Scan this PC”.</Note>}
        <div style={{ flex: 1, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 6 }}>
          {items.map((a) => (
            <div key={a.id} onClick={() => setSel(a)} className="row"
              style={{ gap: 8, padding: '5px 8px', borderBottom: '1px solid var(--line)', cursor: 'pointer', fontSize: 12, background: sel?.id === a.id ? 'rgba(108,140,255,.12)' : undefined }}>
              <span className="chip" style={{ fontSize: 10, flex: 'none' }}>{a.ext.slice(1).toUpperCase()}</span>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={a.path}>{a.name}</span>
              <span className="faint" style={{ flex: 'none', fontSize: 11 }}>{KIND_LABEL[a.kind] || a.kind}{a.n_anims ? ` · ${a.n_anims} clip${a.n_anims > 1 ? 's' : ''}` : ''}{a.tags.includes('mixamo') ? ' · Mixamo' : ''}</span>
            </div>
          ))}
          {data && items.length < data.total && <div style={{ padding: 8, textAlign: 'center' }}><button className="btn sm" onClick={() => load(items.length)}>Load more ({data.total - items.length} left)</button></div>}
        </div>
      </div>
      <div style={{ minWidth: 0, overflow: 'auto' }}>
        {!sel ? <Note>Select a file to preview it. GLB, glTF and FBX play their first animation; skeleton-only files show the bones.</Note> : (
          <div className="panel">
            <div className="panel-title"><span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sel.name}</span></div>
            <div style={{ padding: 10 }}>
              {sel.view_url ? <GlbViewer key={sel.id} url={sel.view_url} format={fmt} height={300} /> : <Note>No preview for {sel.ext} files.</Note>}
              <div className="faint" style={{ fontSize: 11, margin: '6px 0', wordBreak: 'break-all' }}>{sel.path}</div>
              <div style={{ fontSize: 12 }}>
                {KIND_LABEL[sel.kind] || sel.kind} · {fmtSize(sel.size)}{sel.copies > 1 ? ` · ${sel.copies} identical copies on disk` : ''}
                {sel.clips.length > 0 && <div className="faint" style={{ marginTop: 4 }}>Clips: {sel.clips.slice(0, 12).join(', ')}{sel.clips.length > 12 ? ` +${sel.clips.length - 12}` : ''}</div>}
              </div>
              <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                {sel.kind !== 'unknown' && (
                  <button className="btn primary" disabled={imp.busy} onClick={() => toAnim(sel)}>
                    <PersonStanding size={14} />{sel.n_anims ? 'Import animation' : 'Import as character'}
                  </button>
                )}
                <button className="btn sm" onClick={() => setEng(true)}><Gamepad2 size={13} />Export to engine</button>
                <a className="btn ghost sm" href={`/api/models/local/${sel.id}/file`} download={`${sel.name}${sel.ext}`}>Download</a>
              </div>
              <JobBar job={imp.job} error={imp.error} onCancel={imp.cancel} />
              {eng && <EngineExport asset={{ type: 'model', ref: `local:${sel.id}` }} defaultName={sel.name} onClose={() => setEng(false)} />}
              {msg && <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>{msg}</div>}
              <div className="faint" style={{ fontSize: 11, marginTop: 8 }}>
                Importing copies the motion (or the character) into the Animation library, where it can be retargeted, previewed and exported. The original file is not changed.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
