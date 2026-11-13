import { useEffect, useMemo, useState } from 'react'
import { Clapperboard, Download, FilePlus2, Film, Search, Sparkles, Trash2, Wand2 } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'
import { emit } from '../../shared/bus'
import type { MediaRow } from '../../types/api'
import type { AnimClip, AnimRig } from './animApi'
import { scoreColor } from './animApi'
import { ClipPreview, Issues, MetricsTable, Note } from './animUi'

interface Props {
  rigs: AnimRig[]
  clips: AnimClip[]
  reload: () => void
  onRetarget: (clipId: string) => void
  onOpenRig: (rigId: string) => void
}

interface ReviewResult {
  mode: string
  score?: number
  issues: { severity?: string; message?: string; suggestion?: string }[]
  suggestions: string[]
  sheet?: { mime: string; data_base64: string }
  frames: unknown[]
  rubric: string[]
  llm: { used: boolean; error?: string; hint?: string; provider?: string; model?: string }
  instructions?: string
}

export default function LibrarySection({ rigs, clips, reload, onRetarget, onOpenRig }: Props) {
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<'all' | 'retargeted' | 'source'>('all')
  const [rigFilter, setRigFilter] = useState('')
  const [hits, setHits] = useState<AnimClip[] | null>(null)
  const [mode, setMode] = useState('')
  const [selId, setSelId] = useState<string | null>(null)
  const [importPath, setImportPath] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [review, setReview] = useState<ReviewResult | null>(null)
  const [reviewMode, setReviewMode] = useState<'host' | 'local' | 'cloud'>('host')
  const [useCloud, setUseCloud] = useState(false)
  const { job, error, run, cancel, busy } = useJob<any>()

  useEffect(() => {
    if (!q.trim()) { setHits(null); setMode(''); return }
    const t = setTimeout(() => {
      api.get<{ mode: string; hits: { clip: AnimClip }[] }>(`/api/anim/search?q=${encodeURIComponent(q)}&limit=60`)
        .then((r) => { setHits(r.hits.map((h) => h.clip)); setMode(r.mode) }).catch(() => { setHits([]); setMode('') })
    }, 300)
    return () => clearTimeout(t)
  }, [q, clips.length])

  const list = useMemo(() => {
    const base = hits ?? clips
    return base.filter((c) => (kind === 'all' || c.kind === kind) && (!rigFilter || c.rig_id === rigFilter))
  }, [hits, clips, kind, rigFilter])
  const sel = clips.find((c) => c.id === selId) || null
  const rigName = (id: string | null) => rigs.find((r) => r.id === id)?.name

  const importClip = async () => {
    if (!importPath.trim()) return
    setMsg(null)
    try { await run(() => api.post('/api/anim/clips/import', { path: importPath.trim() })); setImportPath(''); reload() } catch { /* JobBar */ }
  }
  const demo = async (kindName: string) => { try { await run(() => api.post('/api/anim/clips/demo', { kind: kindName })); reload() } catch { /* JobBar */ } }
  const remove = async (c: AnimClip) => {
    if (!window.confirm(`Delete "${c.name}"?`)) return
    await api.del(`/api/anim/clips/${c.id}`).catch((e) => setMsg(e.message)); if (selId === c.id) setSelId(null); reload()
  }
  const useInVideo = async (c: AnimClip) => {
    setMsg(null)
    try {
      const done = await run(() => api.post('/api/anim/preview', { clipId: c.id, camera: 'side', size: 512, bg: 'transparent' }))
      const media = done.result?.media as MediaRow | undefined
      if (media) { reload(); emit('cutlab:add-to-timeline', { media }) }
    } catch { /* JobBar */ }
  }
  const renderPreview = async (c: AnimClip, camera: string) => { try { await run(() => api.post('/api/anim/preview', { clipId: c.id, camera, size: 384, bg: 'transparent' })); reload() } catch { /* JobBar */ } }
  const annotate = async (c: AnimClip) => { try { await run(() => api.post('/api/anim/annotate', { clipId: c.id, use_cloud: useCloud })); reload() } catch { /* JobBar */ } }
  const doReview = async (c: AnimClip) => {
    setReview(null)
    try { const d = await run(() => api.post('/api/anim/review', { clipId: c.id, mode: reviewMode })); setReview(d.result as ReviewResult); reload() } catch { /* JobBar */ }
  }

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      <div style={{ flex: 1, minWidth: 0, overflow: 'auto', padding: 14 }}>
        <div className="row" style={{ marginBottom: 10, flexWrap: 'wrap' }}>
          <div className="row grow" style={{ minWidth: 220, position: 'relative' }}>
            <Search size={14} style={{ position: 'absolute', left: 9, opacity: .6 }} />
            <input className="input" style={{ paddingLeft: 28 }} placeholder="Search animations: “bear walking”, “run loop”, tag…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="seg-tabs">
            {(['all', 'retargeted', 'source'] as const).map((k) => <button key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>{k === 'all' ? 'All' : k === 'retargeted' ? 'Retargeted' : 'Source motions'}</button>)}
          </div>
          <select className="select" style={{ width: 160 }} value={rigFilter} onChange={(e) => setRigFilter(e.target.value)}>
            <option value="">All characters</option>
            {rigs.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </div>
        {mode && <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>Search mode: {mode}</div>}
        <div className="row" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
          <input className="input grow" style={{ minWidth: 260 }} placeholder="Import motion: full path of a .bvh / .fbx / .glb / .blend or a motion .npz" value={importPath} onChange={(e) => setImportPath(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && importClip()} />
          <button className="btn" disabled={busy || !importPath.trim()} onClick={importClip}><FilePlus2 size={14} />Import motion</button>
          <button className="btn ghost" disabled={busy} onClick={() => demo('quadruped_walk')} title="Synthetic 4-beat walk (no model needed)">+ Quadruped demo</button>
          <button className="btn ghost" disabled={busy} onClick={() => demo('biped_walk')} title="Synthetic biped walk">+ Biped demo</button>
        </div>
        <JobBar job={job} error={error} onCancel={cancel} />
        {msg && <Note tone="err">{msg}</Note>}

        <h4 style={{ margin: '14px 0 8px' }}>Animation clips <span className="faint" style={{ fontWeight: 400 }}>({list.length})</span></h4>
        {!list.length && <div className="faint" style={{ padding: 24, textAlign: 'center' }}>No clips yet. Import a motion above, add a demo clip, or extract motion from a video (From Video).</div>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10 }}>
          {list.map((c) => (
            <div key={c.id} className="media-card" style={{ borderColor: c.id === selId ? 'var(--accent)' : undefined }} onClick={() => { setSelId(c.id); setReview(null) }}>
              <ClipPreview clip={c} />
              <div style={{ padding: '6px 8px' }}>
                <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={c.name}>{c.name}</div>
                <div className="faint" style={{ fontSize: 11, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{rigName(c.rig_id) ? `on ${rigName(c.rig_id)}` : c.skeleton}{c.role ? ` · ${c.role}` : ''}</div>
                <div className="row" style={{ flexWrap: 'wrap', gap: 3, marginTop: 4 }}>
                  {c.tags.slice(0, 4).map((t) => <span key={t} className="chip" style={{ fontSize: 10 }}>{t}</span>)}
                </div>
              </div>
            </div>
          ))}
        </div>

        <h4 style={{ margin: '20px 0 8px' }}>Characters (rigs) <span className="faint" style={{ fontWeight: 400 }}>({rigs.length})</span></h4>
        {!rigs.length && <div className="faint">No rigs yet — add one in the Rigs section or generate one in Generate.</div>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10 }}>
          {rigs.map((r) => (
            <div key={r.id} className="media-card" onClick={() => onOpenRig(r.id)}>
              <div style={{ height: 100, background: '#14161c', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {r.thumb_url ? <img src={r.thumb_url} alt="" style={{ height: '100%', objectFit: 'contain' }} /> : <span className="faint">no thumbnail</span>}
              </div>
              <div style={{ padding: '4px 8px' }}>
                <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.name}</div>
                <div className="faint" style={{ fontSize: 11 }}>{r.kind} · {r.bone_count} bones</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {sel && (
        <aside style={{ width: 330, flex: 'none', borderLeft: '1px solid var(--line)', background: 'var(--bg-1)', overflow: 'auto', padding: 12 }}>
          <ClipDetail key={sel.id} clip={sel} rigName={rigName(sel.rig_id)} busy={busy} onSaved={reload}
            onRetarget={() => onRetarget(sel.id)} onDelete={() => remove(sel)} onUse={() => useInVideo(sel)} onPreview={(cam) => renderPreview(sel, cam)}
            onAnnotate={() => annotate(sel)} onReview={() => doReview(sel)} reviewMode={reviewMode} setReviewMode={setReviewMode} useCloud={useCloud} setUseCloud={setUseCloud} />
          {review && <ReviewView r={review} />}
        </aside>
      )}
    </div>
  )
}

function ClipDetail(p: {
  clip: AnimClip; rigName?: string; busy: boolean; onSaved: () => void; onRetarget: () => void; onDelete: () => void; onUse: () => void
  onPreview: (cam: string) => void; onAnnotate: () => void; onReview: () => void
  reviewMode: 'host' | 'local' | 'cloud'; setReviewMode: (m: 'host' | 'local' | 'cloud') => void; useCloud: boolean; setUseCloud: (b: boolean) => void
}) {
  const c = p.clip
  const [name, setName] = useState(c.name)
  const [tags, setTags] = useState(c.tags.join(', '))
  const [caption, setCaption] = useState(c.caption)
  const [role, setRole] = useState(c.role)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { setTags(c.tags.join(', ')); setCaption(c.caption) }, [c.tags, c.caption])
  const save = async () => {
    setErr(null)
    try {
      await api.patch(`/api/anim/clips/${c.id}`, { name, role, caption, tags: tags.split(',').map((t) => t.trim()).filter(Boolean) })
      p.onSaved()
    } catch (e: any) { setErr(e.message) }
  }
  return (
    <div>
      <ClipPreview clip={c} height={160} />
      <div className="field" style={{ marginTop: 10 }}><label>Name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
      <div className="row" style={{ gap: 8 }}>
        <div className="field grow"><label>Game role (clip name in exports)</label>
          <select className="select" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">Custom (keep name)</option>
            {['idle', 'walk', 'run', 'sprint', 'jump', 'fall', 'land', 'attack', 'hit', 'die', 'crouch', 'swim', 'climb', 'roll', 'dodge', 'block', 'cast', 'interact', 'turn', 'strafe', 'trot', 'gallop'].map((r) => <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>)}
          </select></div>
      </div>
      <div className="field"><label>Tags (comma separated)</label><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></div>
      <div className="field"><label>Caption</label><textarea className="textarea" rows={3} value={caption} onChange={(e) => setCaption(e.target.value)} /></div>
      <div className="row" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
        <button className="btn sm primary" onClick={save}>Save</button>
        <button className="btn sm" disabled={p.busy} onClick={p.onAnnotate} title="Write tags + caption with the language model"><Sparkles size={12} />Annotate</button>
        <label className="faint row" style={{ fontSize: 11, gap: 4 }}><input type="checkbox" checked={p.useCloud} onChange={(e) => p.setUseCloud(e.target.checked)} />use cloud (sends 3 frames)</label>
      </div>
      {err && <div className="err">{err}</div>}
      <div className="faint" style={{ fontSize: 11, margin: '6px 0' }}>{c.kind === 'retargeted' ? `Retargeted on ${p.rigName || '?'}` : `Source motion · ${c.skeleton}`} · {c.frames} frames @ {c.fps.toFixed(0)} fps · {c.root_motion ? 'root motion' : 'in place'}{c.loop ? ' · loop' : ''}</div>
      <div className="row" style={{ flexWrap: 'wrap', marginBottom: 10 }}>
        {c.kind === 'source'
          ? <button className="btn sm primary" onClick={p.onRetarget}><Wand2 size={12} />Retarget onto a rig</button>
          : <>
            <button className="btn sm primary" disabled={p.busy} onClick={p.onUse} title="Render a transparent preview and put it on the video timeline"><Clapperboard size={12} />Use in video</button>
            <select className="select" style={{ width: 120 }} defaultValue="" onChange={(e) => { if (e.target.value) { p.onPreview(e.target.value); e.target.value = '' } }}>
              <option value="">Re-render…</option>
              {['side', 'front', 'back', 'three_quarter', 'orbit'].map((x) => <option key={x} value={x}>{x.replace('_', ' ')}</option>)}
            </select>
          </>}
        <button className="btn sm danger" onClick={p.onDelete}><Trash2 size={12} />Delete</button>
      </div>
      {c.kind === 'retargeted' && (
        <>
          <div className="row" style={{ marginBottom: 8 }}>
            <select className="select" style={{ width: 130 }} value={p.reviewMode} onChange={(e) => p.setReviewMode(e.target.value as 'host' | 'local' | 'cloud')}>
              <option value="host">Review: host (no AI call)</option><option value="local">Review: local model</option><option value="cloud">Review: cloud model</option>
            </select>
            <button className="btn sm" disabled={p.busy} onClick={p.onReview}><Film size={12} />Review</button>
          </div>
          {p.reviewMode === 'cloud' && <Note tone="warn">Cloud review uploads rendered frames to Anthropic.</Note>}
          <div style={{ fontWeight: 600, margin: '6px 0 4px' }}>Quality metrics <span style={{ color: scoreColor(c.score) }}>{c.score != null ? `· score ${c.score.toFixed(0)}` : ''}</span></div>
          <MetricsTable metrics={c.stats} />
          <div style={{ marginTop: 6 }}><Issues items={c.stats.issues || []} /></div>
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {Object.entries(c.files).filter(([k]) => ['glb', 'fbx'].includes(k)).map(([k, u]) => <a key={k} className="btn sm" href={`${u}?download=1`}><Download size={12} />{k.toUpperCase()}</a>)}
          </div>
        </>
      )}
    </div>
  )
}

function ReviewView({ r }: { r: ReviewResult }) {
  return (
    <div style={{ marginTop: 14, borderTop: '1px solid var(--line)', paddingTop: 10 }}>
      <b>Review ({r.mode}){r.score != null ? ` · score ${r.score}` : ''}</b>
      {r.instructions && <Note>{r.instructions}</Note>}
      {r.llm?.error && <Note tone="warn">{r.llm.error}{r.llm.hint ? ` — ${r.llm.hint}` : ''}</Note>}
      {r.sheet && <img alt="frame sheet" src={`data:${r.sheet.mime};base64,${r.sheet.data_base64}`} style={{ width: '100%', borderRadius: 6, margin: '6px 0' }} />}
      <Issues items={r.issues} />
      {r.suggestions.length > 0 && <ul style={{ fontSize: 12, paddingLeft: 18 }}>{r.suggestions.map((s, i) => <li key={i}>{s}</li>)}</ul>}
    </div>
  )
}
