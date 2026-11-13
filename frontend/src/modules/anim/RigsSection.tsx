import { useCallback, useEffect, useMemo, useState } from 'react'
import { PersonStanding, Save, Sparkles, Trash2, Upload, Wand2 } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'
import type { AnimClip, AnimRig, BoneMapResp, RigFull } from './animApi'
import { SKELETONS, confColor } from './animApi'
import { Note } from './animUi'

interface Props {
  rigs: AnimRig[]
  clips: AnimClip[]
  selectedId: string | null
  onSelect: (id: string | null) => void
  reload: () => void
}

export default function RigsSection({ rigs, clips, selectedId, onSelect, reload }: Props) {
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [yUp, setYUp] = useState(false)
  const { job, error, run, cancel, busy } = useJob<any>()

  const importRig = async () => {
    if (!path.trim()) return
    try {
      const d = await run(() => api.post('/api/anim/rigs/import', { path: path.trim(), name: name.trim() || undefined, up_axis: yUp ? 'Y' : undefined }))
      setPath(''); setName(''); reload()
      const id = d.result?.rig?.id as string | undefined
      if (id) onSelect(id)
    } catch { /* JobBar */ }
  }

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      <div style={{ width: 300, flex: 'none', borderRight: '1px solid var(--line)', overflow: 'auto', padding: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Add a character</div>
        <div className="field"><label>Model file (full path: .glb .gltf .fbx .blend)</label><input className="input" value={path} onChange={(e) => setPath(e.target.value)} placeholder="D:\models\bear.glb" /></div>
        <div className="field"><label>Name (optional)</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <label className="faint row" style={{ fontSize: 11, gap: 4, marginBottom: 8 }}><input type="checkbox" checked={yUp} onChange={(e) => setYUp(e.target.checked)} />Model lies on its side (Y-up .blend): rotate upright</label>
        <button className="btn primary" disabled={busy || !path.trim()} onClick={importRig}><Upload size={14} />Import rig</button>
        <JobBar job={job} error={error} onCancel={cancel} />
        <Note>The rig is analysed in Blender: scale, up axis and facing are normalised (feet on the floor, facing −Y / +Z in glTF) and a thumbnail is rendered.</Note>
        <div style={{ fontWeight: 600, margin: '14px 0 6px' }}>Characters</div>
        {!rigs.length && <div className="faint">None yet.</div>}
        {rigs.map((r) => (
          <div key={r.id} className="row" onClick={() => onSelect(r.id)} style={{ padding: 6, borderRadius: 6, cursor: 'pointer', background: r.id === selectedId ? 'var(--accent-soft)' : undefined, marginBottom: 4 }}>
            <div style={{ width: 44, height: 44, background: '#14161c', borderRadius: 4, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {r.thumb_url ? <img src={r.thumb_url} alt="" style={{ maxWidth: '100%', maxHeight: '100%' }} /> : <PersonStanding size={18} className="faint" />}
            </div>
            <div className="grow"><div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.name}</div>
              <div className="faint" style={{ fontSize: 11 }}>{r.kind} · {r.bone_count} bones · {r.mesh_count} mesh</div></div>
          </div>
        ))}
      </div>
      <div style={{ flex: 1, minWidth: 0, overflow: 'auto', padding: 14 }}>
        {selectedId ? <RigDetail key={selectedId} rigId={selectedId} clips={clips} reload={reload} onDeleted={() => { onSelect(null); reload() }} /> : <div className="faint" style={{ padding: 40, textAlign: 'center' }}>Select or import a character to edit its bone map.</div>}
      </div>
    </div>
  )
}

function RigDetail({ rigId, clips, reload, onDeleted }: { rigId: string; clips: AnimClip[]; reload: () => void; onDeleted: () => void }) {
  const [rig, setRig] = useState<RigFull | null>(null)
  const [skel, setSkel] = useState('ap10k17')
  const [bm, setBm] = useState<BoneMapResp | null>(null)
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [useLlm, setUseLlm] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err' | 'warn'; text: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const sourceClips = clips.filter((c) => c.kind === 'source')

  const q = useCallback((s: string) => (s.startsWith('clip:') ? `clipId=${s.slice(5)}` : `skeleton=${s}`), [])
  const loadMap = useCallback(async (s: string) => {
    try { const r = await api.get<BoneMapResp>(`/api/anim/rigs/${rigId}/bonemap?${q(s)}`); setBm(r); setEdit(r.map); setDirty(false) } catch (e: any) { setMsg({ tone: 'err', text: e.message }) }
  }, [rigId, q])
  useEffect(() => { api.get<RigFull>(`/api/anim/rigs/${rigId}`).then((r) => { setRig(r); setSkel(r.kind === 'quadruped' ? 'ap10k17' : 'smpl24') }).catch((e) => setMsg({ tone: 'err', text: e.message })) }, [rigId])
  useEffect(() => { if (rig) loadMap(skel) }, [rig, skel, loadMap])

  const suggest = async () => {
    setLoading(true); setMsg(null)
    try {
      const body = skel.startsWith('clip:') ? { clipId: skel.slice(5), use_llm: useLlm } : { skeleton: skel, use_llm: useLlm }
      const r = await api.post<BoneMapResp>(`/api/anim/rigs/${rigId}/suggest-map`, body)
      setBm((o) => (o ? { ...o, ...r, source_joints: r.source_joints ?? o.source_joints, bones: o.bones } : r)); setEdit(r.map); setDirty(true)
      setMsg({ tone: r.overall >= 0.8 ? 'ok' : 'warn', text: `Suggested ${Object.keys(r.map).length} mappings (confidence ${(r.overall * 100).toFixed(0)}%). Review, then Save.` })
    } catch (e: any) { setMsg({ tone: 'err', text: e.message }) }
    setLoading(false)
  }
  const save = async () => {
    setMsg(null)
    try {
      const body = { map: edit, ...(skel.startsWith('clip:') ? { clipId: skel.slice(5) } : { skeleton: skel }) }
      const r = await api.put<BoneMapResp>(`/api/anim/rigs/${rigId}/bonemap`, body)
      setBm(r); setEdit(r.map); setDirty(false); reload(); setMsg({ tone: 'ok', text: 'Bone map saved.' })
    } catch (e: any) { setMsg({ tone: 'err', text: e.message }) }
  }
  const del = async () => {
    if (!rig || !window.confirm(`Delete "${rig.name}" and all clips retargeted onto it?`)) return
    await api.del(`/api/anim/rigs/${rigId}`).catch((e) => setMsg({ tone: 'err', text: e.message })); onDeleted()
  }

  const dupes = useMemo(() => {
    const cnt: Record<string, number> = {}
    Object.values(edit).forEach((j) => { if (j) cnt[j] = (cnt[j] || 0) + 1 })
    return new Set(Object.keys(cnt).filter((j) => cnt[j] > 1))
  }, [edit])

  if (!rig) return <div className="faint">Loading…</div>
  const joints = bm?.source_joints ?? []
  const bones = rig.bones
  const conf = (b: string) => (dirty && edit[b] !== bm?.map[b] ? 1 : bm?.confidence[b])

  return (
    <div>
      <div className="row" style={{ alignItems: 'flex-start', gap: 14, marginBottom: 12 }}>
        <div style={{ width: 150, height: 150, background: '#14161c', borderRadius: 8, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {rig.thumb_url ? <img src={rig.thumb_url} alt="" style={{ maxWidth: '100%', maxHeight: '100%' }} /> : <PersonStanding size={40} className="faint" />}
        </div>
        <div className="grow">
          <h3 style={{ margin: '0 0 4px' }}>{rig.name}</h3>
          <div className="muted" style={{ fontSize: 12 }}>{rig.kind} · {rig.bone_count} bones · {rig.mesh_count} mesh · {rig.height.toFixed(2)} m tall</div>
          {(rig.notes || []).map((n, i) => <div key={i} className="faint" style={{ fontSize: 11, marginTop: 2 }}>• {n}</div>)}
          <button className="btn sm danger" style={{ marginTop: 8 }} onClick={del}><Trash2 size={12} />Delete rig</button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title"><span className="grow">Bone map</span>
          {bm && <span className="chip" style={{ color: confColor(bm.overall) }}>confidence {(bm.overall * 100).toFixed(0)}%</span>}
          {bm?.source && <span className="chip">{bm.source}</span>}
        </div>
        <div style={{ padding: 12 }}>
          <div className="row" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
            <label className="faint" style={{ fontSize: 12 }}>Source skeleton</label>
            <select className="select" style={{ width: 280 }} value={skel} onChange={(e) => setSkel(e.target.value)}>
              {SKELETONS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              {sourceClips.length > 0 && <optgroup label="Skeleton of an imported clip">{sourceClips.map((c) => <option key={c.id} value={`clip:${c.id}`}>{c.name} ({c.skeleton})</option>)}</optgroup>}
            </select>
            <button className="btn sm" disabled={loading} onClick={suggest}><Wand2 size={12} />Suggest</button>
            <label className="faint row" style={{ fontSize: 11, gap: 4 }} title="Only bone names and hierarchy are sent to the language model, never images."><input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} /><Sparkles size={11} />refine with LLM</label>
            <div className="grow" />
            <button className="btn sm primary" disabled={!dirty} onClick={save}><Save size={12} />Save</button>
          </div>
          <div className="faint" style={{ fontSize: 11, marginBottom: 8 }}>Each bone is driven by the source joint where its <b>head</b> (start) sits. Unmapped bones follow their neighbours automatically (tails, ears, spine fill-ins).</div>
          {msg && <Note tone={msg.tone}>{msg.text}</Note>}
          {dupes.size > 0 && <Note tone="warn">A joint is used twice: {[...dupes].join(', ')}. Only one bone per joint is allowed.</Note>}
          {bm?.notes?.map((n, i) => <div key={i} className="faint" style={{ fontSize: 11 }}>• {n}</div>)}
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8, fontSize: 12 }}>
            <thead><tr className="faint" style={{ textAlign: 'left' }}><th style={{ padding: '4px 6px' }}>Rig bone</th><th>Source joint</th><th style={{ width: 90 }}>Confidence</th></tr></thead>
            <tbody>
              {bones.map((b) => {
                const j = edit[b.name] || ''
                const c = j ? conf(b.name) : undefined
                return (
                  <tr key={b.name} style={{ borderTop: '1px solid var(--line)', background: j && dupes.has(j) ? 'rgba(245,184,61,.12)' : undefined }}>
                    <td style={{ padding: '3px 6px', paddingLeft: 6 + b.depth * 10, whiteSpace: 'nowrap' }} title={b.name}>{b.name}</td>
                    <td><select className="select" style={{ padding: '2px 6px' }} value={j} onChange={(e) => { setEdit((o) => ({ ...o, [b.name]: e.target.value })); setDirty(true) }}>
                      <option value="">— none (follows parent) —</option>{joints.map((x) => <option key={x} value={x}>{x}</option>)}</select></td>
                    <td>{c != null ? <span style={{ color: confColor(c) }}>● {(c * 100).toFixed(0)}%</span> : <span className="faint">–</span>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
