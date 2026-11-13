import { useEffect, useState } from 'react'
import { Download, Gamepad2, Play, Send } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import type { AnimClip, AnimRig, AnimStatus, BoneMapResp } from './animApi'
import { ClipPreview, Issues, MetricsTable, Note, Section } from './animUi'
import EngineExport from '../models/EngineExport'

interface Props {
  rigs: AnimRig[]
  clips: AnimClip[]
  status: AnimStatus | null
  reload: () => void
  reloadStatus: () => void
  clipId: string
  rigId: string
  setClipId: (id: string) => void
  setRigId: (id: string) => void
  onEditMap: (rigId: string) => void
}

interface RetargetResult { clip: AnimClip; metrics: Record<string, any>; notes: string[]; media?: MediaRow; preview_error?: string }
interface ExportResult {
  file: string; url: string; sidecar_url?: string; format: string; clips: { name: string; duration: number; rootMotion: boolean; loop: boolean }[]
  verify?: { ok: boolean; problems: string[]; actions?: unknown[] }; exports: { url: string; sidecar_url?: string; file: string; verify?: { ok: boolean } }[]
}
interface SendResult { sent: boolean; filename: string; server: string; asset: Record<string, any> }

export default function RetargetSection(p: Props) {
  const sources = p.clips.filter((c) => c.kind === 'source')
  const rigClips = p.clips.filter((c) => c.kind === 'retargeted' && c.rig_id === p.rigId)
  const [engOpen, setEngOpen] = useState(false)
  const [opt, setOpt] = useState({ in_place: false, loop: true, scale: 1, foot_lock: true, ground_fix: true, smooth: 0.5, fps: '', name: '', preview: true, use_llm: false })
  const [bm, setBm] = useState<BoneMapResp | null>(null)
  const [result, setResult] = useState<RetargetResult | null>(null)
  const rt = useJob<RetargetResult>()
  const ex = useJob<ExportResult>()
  const sd = useJob<SendResult>()
  const [exp, setExp] = useState({ format: 'glb', all: true, perClip: false, rootMotion: false, fps: 30, preset: 'game' })
  const [exportResult, setExportResult] = useState<ExportResult | null>(null)
  const [sendResult, setSendResult] = useState<SendResult | null>(null)
  const [sendErr, setSendErr] = useState<string | null>(null)
  const set = <K extends keyof typeof opt>(k: K, v: (typeof opt)[K]) => setOpt((o) => ({ ...o, [k]: v }))

  useEffect(() => {
    if (!p.rigId || !p.clipId) { setBm(null); return }
    api.get<BoneMapResp>(`/api/anim/rigs/${p.rigId}/bonemap?clipId=${p.clipId}`).then(setBm).catch(() => setBm(null))
  }, [p.rigId, p.clipId])

  const run = async () => {
    setResult(null)
    try {
      const d = await rt.run(() => api.post('/api/anim/retarget', {
        clipId: p.clipId, rigId: p.rigId,
        options: { in_place: opt.in_place, loop: opt.loop, scale: opt.scale, foot_lock: opt.foot_lock, ground_fix: opt.ground_fix, smooth: opt.smooth, fps: opt.fps ? Number(opt.fps) : undefined, name: opt.name || undefined, preview: opt.preview, use_llm: opt.use_llm },
      }))
      setResult(d.result as RetargetResult); p.reload()
    } catch { /* JobBar */ }
  }
  const doExport = async () => {
    setExportResult(null)
    const ids = rigClips.map((c) => c.id)
    try {
      const d = await ex.run(() => api.post('/api/anim/export', {
        rigId: p.rigId, clipIds: exp.all ? undefined : (result ? [result.clip.id] : ids.slice(0, 1)), format: exp.format,
        options: { preset: exp.preset, all_actions: exp.all, per_clip: exp.perClip, root_motion: exp.rootMotion, fps: exp.fps },
      }))
      setExportResult(d.result as ExportResult); p.reload()
    } catch { /* JobBar */ }
  }
  const sendEngine = async () => {
    setSendResult(null); setSendErr(null)
    try {
      const d = await sd.run(() => api.post('/api/anim/send/my-engine', { rigId: p.rigId, clipIds: exp.all ? undefined : (result ? [result.clip.id] : undefined), options: { fps: exp.fps, root_motion: exp.rootMotion } }))
      setSendResult(d.result as SendResult)
    } catch (e: any) { setSendErr(e.message); p.reloadStatus() }
  }
  const setRole = async (c: AnimClip, role: string) => { await api.patch(`/api/anim/clips/${c.id}`, { role }).catch(() => undefined); p.reload() }

  const ready = !!p.clipId && !!p.rigId && !rt.busy
  const preview = result?.media

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1fr) minmax(320px, 1fr)', gap: 14 }}>
        <div>
          <Section title="1 · Retarget a motion onto a character">
            <div className="field"><label>Source motion</label>
              <select className="select" value={p.clipId} onChange={(e) => p.setClipId(e.target.value)}>
                <option value="">— choose a motion clip —</option>
                {sources.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.skeleton} · {c.duration.toFixed(1)}s</option>)}
              </select></div>
            <div className="field"><label>Character (rig)</label>
              <select className="select" value={p.rigId} onChange={(e) => p.setRigId(e.target.value)}>
                <option value="">— choose a rig —</option>
                {p.rigs.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.kind})</option>)}
              </select></div>
            {bm && (
              <div className="row" style={{ marginBottom: 8, flexWrap: 'wrap' }}>
                <span className="chip">{Object.keys(bm.map).length} bones mapped</span>
                <span className="chip" style={{ color: bm.overall >= 0.8 ? 'var(--ok)' : 'var(--warn)' }}>confidence {(bm.overall * 100).toFixed(0)}%</span>
                <button className="btn sm ghost" onClick={() => p.onEditMap(p.rigId)}>Edit bone map…</button>
              </div>
            )}
            {bm && bm.overall < 0.6 && <Note tone="warn">The automatic bone map looks weak for this pair. Open the bone-map editor and check the mapping first.</Note>}
            <div className="row" style={{ flexWrap: 'wrap', gap: 14, marginBottom: 6 }}>
              <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={opt.in_place} onChange={(e) => set('in_place', e.target.checked)} />In place (no root motion)</label>
              <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={opt.loop} onChange={(e) => set('loop', e.target.checked)} />Close the loop</label>
              <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={opt.foot_lock} onChange={(e) => set('foot_lock', e.target.checked)} />Foot lock (IK)</label>
              <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={opt.ground_fix} onChange={(e) => set('ground_fix', e.target.checked)} />Ground fix</label>
            </div>
            <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
              <div className="field grow"><label>Smoothing: {opt.smooth.toFixed(2)}</label><input type="range" min={0} max={1} step={0.05} value={opt.smooth} onChange={(e) => set('smooth', +e.target.value)} /></div>
              <div className="field" style={{ width: 90 }}><label>Scale ×</label><input className="input" type="number" step={0.05} min={0.1} max={5} value={opt.scale} onChange={(e) => set('scale', +e.target.value || 1)} /></div>
              <div className="field" style={{ width: 90 }}><label>FPS</label><input className="input" type="number" min={1} max={120} value={opt.fps} placeholder="clip" onChange={(e) => set('fps', e.target.value)} /></div>
            </div>
            <div className="field"><label>Result name (optional)</label><input className="input" value={opt.name} onChange={(e) => set('name', e.target.value)} /></div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 14, marginBottom: 8 }}>
              <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={opt.preview} onChange={(e) => set('preview', e.target.checked)} />Render a transparent preview</label>
              <label className="row faint" style={{ gap: 4, fontSize: 11 }} title="Only bone names are sent"><input type="checkbox" checked={opt.use_llm} onChange={(e) => set('use_llm', e.target.checked)} />refine bone map with LLM</label>
            </div>
            <button className="btn primary" disabled={!ready} onClick={run}><Play size={14} />Retarget</button>
            <JobBar job={rt.job} error={rt.error} onCancel={rt.cancel} />
          </Section>

          {result && (
            <Section title="Result">
              <div className="row" style={{ alignItems: 'flex-start', gap: 12 }}>
                <div style={{ width: 240, flex: 'none' }}>
                  {preview
                    ? <div style={{ background: 'repeating-conic-gradient(#2a2d36 0% 25%, #1c1e26 0% 50%) 50% / 16px 16px', borderRadius: 6 }}>
                      <video src={preview.url} controls loop autoPlay muted style={{ width: '100%', display: 'block', borderRadius: 6 }} /></div>
                    : <ClipPreview clip={result.clip} height={170} />}
                  {result.preview_error && <Note tone="warn">Preview failed: {result.preview_error}</Note>}
                </div>
                <div className="grow" style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>{result.clip.name} <span className="chip">score {result.metrics.score}</span></div>
                  <MetricsTable metrics={result.metrics} />
                </div>
              </div>
              <div style={{ marginTop: 8 }}><Issues items={result.metrics.issues || []} /></div>
              {result.notes.length > 0 && <details style={{ marginTop: 6 }}><summary className="faint" style={{ cursor: 'pointer', fontSize: 11 }}>Solver notes</summary>{result.notes.map((n, i) => <div key={i} className="faint" style={{ fontSize: 11 }}>• {n}</div>)}</details>}
            </Section>
          )}
        </div>

        <div>
          <Section title="2 · Export for a game engine">
            {!p.rigId && <Note>Choose a character on the left.</Note>}
            <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>Retargeted clips on this character ({rigClips.length}). Set a game role to give clips conventional names (Idle, Walk, Run…) in the exported file.</div>
            <div style={{ maxHeight: 190, overflow: 'auto', marginBottom: 8 }}>
              {rigClips.map((c) => (
                <div key={c.id} className="row" style={{ padding: '3px 0', borderBottom: '1px solid var(--line)' }}>
                  <span className="grow" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={c.name}>{c.name}</span>
                  <span className="faint" style={{ fontSize: 11 }}>{c.duration.toFixed(1)}s</span>
                  <select className="select" style={{ width: 100, padding: '2px 4px' }} value={c.role} onChange={(e) => setRole(c, e.target.value)}>
                    <option value="">name</option>
                    {Object.keys(p.status?.role_names || { idle: 1, walk: 1, run: 1, jump: 1, attack: 1 }).filter((k) => !['death'].includes(k)).map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
              ))}
              {!rigClips.length && <div className="faint">No retargeted clips yet.</div>}
            </div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 12, marginBottom: 6 }}>
              <div className="seg-tabs">{['glb', 'fbx', 'blend'].map((f) => <button key={f} className={exp.format === f ? 'on' : ''} onClick={() => setExp({ ...exp, format: f })}>{f.toUpperCase()}</button>)}</div>
              <div className="field" style={{ width: 80, marginBottom: 0 }}><label>FPS</label><input className="input" type="number" value={exp.fps} min={10} max={120} onChange={(e) => setExp({ ...exp, fps: +e.target.value || 30 })} /></div>
            </div>
            <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={exp.all} onChange={(e) => setExp({ ...exp, all: e.target.checked })} />Include all clips of this character (one file, all animations)</label>
            <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={exp.perClip} onChange={(e) => setExp({ ...exp, perClip: e.target.checked })} />One armature-only file per clip</label>
            <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={exp.rootMotion} onChange={(e) => setExp({ ...exp, rootMotion: e.target.checked })} />Keep root motion (default: in place, the game moves the character)</label>
            <div className="faint" style={{ fontSize: 11, margin: '6px 0' }}>Game preset: Y-up, metres, facing +Z, feet at the origin, keys baked at {exp.fps} fps, mesh + skin + all actions in a single {exp.format.toUpperCase()}; a small <code>.anim.json</code> sidecar lists the clips and bones.</div>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              <button className="btn primary" disabled={!p.rigId || ex.busy || !rigClips.length} onClick={doExport}><Gamepad2 size={14} />Export</button>
              <button className="btn" disabled={!p.rigId || !rigClips.length} onClick={() => setEngOpen(true)} title="Write into a Godot / Unity / Unreal / three.js project in the format it imports"><Gamepad2 size={14} />Export to engine project…</button>
              <button className="btn" disabled={!p.rigId || sd.busy || !rigClips.length} onClick={sendEngine} title="POST the GLB to the my-engine asset server"><Send size={14} />Export to my-engine</button>
            </div>
            <JobBar job={ex.job} error={ex.error} onCancel={ex.cancel} />
            {engOpen && p.rigId && <EngineExport asset={{ type: 'character', rig_id: p.rigId, clip_ids: exp.all ? undefined : (result ? [result.clip.id] : rigClips.slice(0, 1).map((c) => c.id)) }}
              defaultName={p.rigs.find((r) => r.id === p.rigId)?.name || 'Character'} onClose={() => setEngOpen(false)} />}
            <JobBar job={sd.job} error={null} onCancel={sd.cancel} />
            {p.status && !p.status.my_engine.reachable && <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>my-engine asset server is not running. Start it with <code>pnpm assets</code> in D:\THreejsEngine\my-engine.</div>}
            {sendErr && <Note tone="err">{sendErr}</Note>}
            {sendResult && <Note tone="ok">Sent {sendResult.filename} to {sendResult.server}.</Note>}
            {exportResult && (
              <div style={{ marginTop: 10 }}>
                {exportResult.verify && (exportResult.verify.ok
                  ? <Note tone="ok">Re-imported and verified: armature, skinned mesh and {exportResult.clips.length} animation(s) present.</Note>
                  : <Note tone="warn">Verification: {exportResult.verify.problems.join('; ')}</Note>)}
                <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                  {exportResult.exports.map((e, i) => (
                    <span key={i} className="row" style={{ gap: 4 }}>
                      <a className="btn sm" href={`${e.url}?download=1`}><Download size={12} />{e.file.split(/[\\/]/).pop()}</a>
                      {e.sidecar_url && <a className="btn sm ghost" href={`${e.sidecar_url}?download=1`}>.anim.json</a>}
                    </span>
                  ))}
                </div>
                <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>{exportResult.clips.map((c) => `${c.name} (${c.duration.toFixed(2)}s${c.rootMotion ? ', root motion' : ''}${c.loop ? ', loop' : ''})`).join(' · ')}</div>
              </div>
            )}
          </Section>
        </div>
      </div>
    </div>
  )
}
