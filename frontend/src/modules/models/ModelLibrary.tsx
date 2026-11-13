import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Download, Gamepad2, Gift, PawPrint, PersonStanding, RefreshCw, Search, Trash2, Upload } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { emit } from '../../shared/bus'
import { JobBar } from '../../shared/ui'
import { Note } from '../anim/animUi'
import GlbViewer from '../anim/providers/GlbViewer'
import type { GlbInfo } from '../anim/providers/GlbViewer'
import * as THREE from 'three'
import EngineExport from './EngineExport'
import StarterPacks from './StarterPacks'
import type { EngineAsset } from './EngineExport'

/** White-model ("clay") look: every mesh gets one matte white material, so shapes and silhouettes read without textures. */
function clayLook(root: THREE.Object3D) {
  const mat = new THREE.MeshStandardMaterial({ color: 0xe8e6e1, roughness: 0.85, metalness: 0 })
  const orig = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>()
  root.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh) { orig.set(m, m.material); m.material = mat } })
  return { dispose: () => { orig.forEach((v, m) => { m.material = v }); mat.dispose() } }
}

export interface ModelItem {
  id: string
  kind: 'character' | 'model' | 'fur'
  name: string
  created: number
  thumb_url: string | null
  view_url: string | null
  download_url?: string
  file?: string
  in_animation?: boolean
  rig_id?: string
  rig_kind?: string
  bones?: number
  height_m?: number
  groom_id?: string
  hair_count?: number
  size?: number
  source: string
  hub_id?: string
  format?: string
  license?: string
  author?: string
}

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'character', label: 'Characters' },
  { id: 'free', label: 'Free (CC0)' },
  { id: 'model', label: 'Generated & uploaded' },
  { id: 'fur', label: 'Fur grooms' },
] as const
type Filter = (typeof FILTERS)[number]['id']

export default function ModelLibrary({ reloadKey, onFur }: { reloadKey: number; onFur: (source: string) => void }) {
  const [items, setItems] = useState<ModelItem[]>([])
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<string>('')
  const [msg, setMsg] = useState('')
  const [info, setInfo] = useState<GlbInfo | null>(null)
  const [clay, setClay] = useState(false)
  const [eng, setEng] = useState<{ asset: EngineAsset; name: string } | null>(null)
  const fileIn = useRef<HTMLInputElement>(null)
  const [packs, setPacks] = useState(false)
  const send = useJob()

  const reload = useCallback(async () => {
    try { setItems(await api.get<ModelItem[]>('/api/models')); setErr('') } catch (e: any) { setErr(e.message || String(e)) }
  }, [])
  useEffect(() => { reload() }, [reload, reloadKey])

  const isHub = (m: ModelItem) => !!m.hub_id
  const match = (m: ModelItem, f: Filter) => f === 'all' || (f === 'free' ? isHub(m) : f === 'model' ? m.kind === 'model' && !isHub(m) : m.kind === f)
  const shown = useMemo(() => items.filter((m) => match(m, filter) && (!q || m.name.toLowerCase().includes(q.toLowerCase()))), [items, filter, q]) // eslint-disable-line react-hooks/exhaustive-deps
  const cur = useMemo(() => items.find((m) => m.id === sel) || shown[0] || null, [items, sel, shown])
  useEffect(() => { setInfo(null); setMsg('') }, [cur?.id])

  const upload = async (f: File) => {
    setMsg('')
    try { const m = await api.upload<ModelItem>('/api/models/upload', f); await reload(); setSel(m.id); setMsg(`Imported ${f.name}.`) } catch (e: any) { setMsg(`Upload failed: ${e.message}`) }
  }

  const toAnimation = async (m: ModelItem) => {
    if (m.rig_id) { emit('cutlab:to-anim-rig', { rigId: m.rig_id }); return }
    if (!m.file && !m.hub_id) return
    try {
      const done = await send.run(() => api.post(m.hub_id ? `/api/models/hub/${m.hub_id}/to-animation` : `/api/models/file/${encodeURIComponent(m.file!)}/to-animation`))
      const rigId = done.result?.rig?.id as string | undefined
      await reload()
      if (rigId) emit('cutlab:to-anim-rig', { rigId })
    } catch { /* JobBar */ }
  }

  const remove = async (m: ModelItem) => {
    if (!m.file || !confirm(`Delete "${m.name}"? The file is removed from disk and cannot be recovered.`)) return
    try { await api.del(`/api/models/file/${encodeURIComponent(m.file)}`); setSel(''); await reload() } catch (e: any) { setMsg(`Delete failed: ${e.message}`) }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1fr) minmax(360px, 1.1fr)', gap: 14, padding: 14, height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
          <div className="row" style={{ gap: 4, flex: 1, minWidth: 180, border: '1px solid var(--line)', borderRadius: 6, padding: '0 8px', background: 'var(--bg-1)' }}>
            <Search size={13} className="faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search models" style={{ border: 0, background: 'transparent', flex: 1, padding: '6px 0' }} />
          </div>
          <button className="btn sm primary" onClick={() => setPacks(true)} title="Free characters and white base meshes (CC0 / CC-BY)"><Gift size={13} />Free characters</button>
          <button className="btn sm" onClick={() => fileIn.current?.click()} title="Import GLB, glTF, FBX, OBJ or .blend"><Upload size={13} />Import</button>
          {packs && <StarterPacks onClose={() => setPacks(false)} onInstalled={reload} />}
          <button className="btn ghost sm" onClick={reload} title="Refresh"><RefreshCw size={13} /></button>
          <input ref={fileIn} type="file" accept=".glb,.gltf,.fbx,.obj,.blend" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
        </div>
        <div className="row" style={{ gap: 4, marginBottom: 8 }}>
          {FILTERS.map((f) => (
            <button key={f.id} className={`btn sm ${filter === f.id ? 'primary' : 'ghost'}`} onClick={() => setFilter(f.id)}>
              {f.label} <span style={{ opacity: 0.6 }}>{items.filter((m) => match(m, f.id)).length}</span>
            </button>
          ))}
        </div>
        {err && <Note tone="warn">{err}</Note>}
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8, alignContent: 'start' }}>
          {!shown.length && <div className="faint" style={{ fontSize: 12, gridColumn: '1 / -1', padding: 12 }}>
            {items.length ? 'Nothing matches.' : 'No models yet. Generate one, import a file, or import a character in Animation → Rigs.'}</div>}
          {shown.map((m) => (
            <button key={m.id} onClick={() => setSel(m.id)} className="panel"
              style={{ padding: 0, textAlign: 'left', cursor: 'pointer', overflow: 'hidden', outline: cur?.id === m.id ? '2px solid var(--accent)' : 'none', minWidth: 0 }}>
              <div style={{ height: 104, background: '#15171d', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {m.thumb_url ? <img src={m.thumb_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                  : <span className="faint" style={{ fontSize: 11 }}>{m.view_url ? '3D preview →' : m.file?.split('.').pop()?.toUpperCase()}</span>}
              </div>
              <div style={{ padding: '6px 8px' }}>
                <div style={{ fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.name}</div>
                <div className="faint" style={{ fontSize: 10.5 }}>
                  {m.kind === 'character' ? `Character · ${m.bones ?? '?'} bones` : m.kind === 'fur' ? 'Fur groom' : m.hub_id ? `Free · ${m.license || 'CC0'}` : m.source === 'generated' ? 'Generated' : 'Uploaded'}
                  {m.in_animation && m.kind !== 'character' ? ' · in Animation' : ''}
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div style={{ minWidth: 0, overflow: 'auto' }}>
        {!cur ? <Note>Select a model to preview it.</Note> : (
          <div className="panel">
            <div className="panel-title"><span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cur.name}</span></div>
            <div style={{ padding: 12 }}>
              {cur.view_url ? <GlbViewer key={`${cur.id}-${clay}`} url={cur.view_url} format={cur.format === 'fbx' ? 'fbx' : 'gltf'} height={360} onInfo={setInfo} onModel={clay ? clayLook : undefined} />
                : <Note>No 3D preview for this file type ({cur.file?.split('.').pop()}). Send it to Animation to convert it.</Note>}
              {cur.view_url && <label className="row" style={{ gap: 4, fontSize: 12, marginTop: 6, cursor: 'pointer' }}><input type="checkbox" checked={clay} onChange={(e) => setClay(e.target.checked)} />White model (clay) view</label>}
              <div className="faint" style={{ fontSize: 11, margin: '6px 0 10px' }}>
                {cur.kind === 'character' && <>Character in the animation rig library · {cur.rig_kind} · {cur.bones} bones{cur.height_m ? ` · ${cur.height_m.toFixed(2)} m tall` : ''}</>}
                {cur.hub_id && <>{cur.source} · {cur.license}{cur.author ? ` · by ${cur.author}` : ''} · free for commercial use</>}
                {cur.kind === 'model' && !cur.hub_id && <>{cur.source === 'generated' ? 'Generated with Tripo / Meshy' : 'Uploaded file'}{cur.size ? ` · ${(cur.size / 1e6).toFixed(1)} MB` : ''}{info ? ` · ${info.bones ? `${info.bones} bones` : 'no skeleton'}` : ''}</>}
                {cur.kind === 'fur' && <>Fur groom{cur.hair_count ? ` · ${cur.hair_count.toLocaleString()} strands` : ''}</>}
              </div>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                {cur.kind !== 'fur' && (
                  <button className="btn primary" disabled={send.busy} onClick={() => toAnimation(cur)}>
                    <PersonStanding size={14} />{cur.rig_id ? 'Open in Animation' : 'Send to Animation'}
                  </button>
                )}
                {cur.kind === 'character' && <button className="btn" onClick={() => onFur(`rig:${cur.rig_id}`)}><PawPrint size={14} />Groom fur</button>}
                {cur.kind === 'fur' && <button className="btn" onClick={() => onFur(`groom:${cur.groom_id}`)}><PawPrint size={14} />Open in Fur</button>}
                {cur.download_url && <a className="btn ghost sm" href={cur.download_url} download={cur.file}><Download size={13} />Download</a>}
                <button className="btn sm" onClick={() => setEng({ name: cur.name, asset: cur.rig_id ? { type: 'character', rig_id: cur.rig_id } : cur.groom_id ? { type: 'fur', groom_id: cur.groom_id } : { type: 'model', ref: cur.hub_id ? `hub:${cur.hub_id}` : `file:${cur.file}` } })}><Gamepad2 size={13} />Export to engine</button>
                {cur.kind === 'model' && !cur.hub_id && <button className="btn ghost sm" onClick={() => remove(cur)} title="Delete file"><Trash2 size={13} /></button>}
              </div>
              <JobBar job={send.job} error={send.error} onCancel={send.cancel} />
              {eng && <EngineExport asset={eng.asset} defaultName={eng.name} onClose={() => setEng(null)} />}
              {msg && <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{msg}</div>}
              {cur.kind === 'model' && !cur.in_animation && !cur.hub_id && (
                <div className="faint" style={{ fontSize: 11, marginTop: 8 }}>
                  "Send to Animation" imports the model into the rig library (Blender checks the skeleton, scale and orientation). Unrigged models
                  need a skeleton first: generate with auto-rig (Tripo rigs quadrupeds, Meshy rigs bipeds).
                </div>
              )}
            </div>
          </div>
        )}
        {msg && !cur && <div className="faint">{msg}</div>}
      </div>
    </div>
  )
}
