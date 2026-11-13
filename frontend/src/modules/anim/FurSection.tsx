import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Clapperboard, Download, Film, Gamepad2, RefreshCw, Save, Scissors } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'
import { emit } from '../../shared/bus'
import type { MediaRow } from '../../types/api'
import { Note, Section } from './animUi'
import GlbViewer from './providers/GlbViewer'
import EngineExport from '../models/EngineExport'
import { addShellFur, DEFAULT_FUR } from './fur/shellFur'
import type { FurParams, ShellFur } from './fur/shellFur'

interface FurSource { id: string; name: string; file: string; rig_id: string | null; animated: boolean; created: number }
interface FurRecord {
  id: string; name: string; created: number
  source: { id: string; name: string; animated: boolean }
  files: Record<string, string | null>; urls: Record<string, string>
  shell: FurParams
  groom: { params: Record<string, any>; hair_count: number; base_length_m: number; height_m: number; render_device?: string }
  videos?: { file: string; media_id: string; camera: string; transparent: boolean; frames: number; fps: number; seconds?: number; device?: string }[]
}
interface VideoResult { media: MediaRow; url: string; frames: number; fps: number; render_seconds: number; device: string }
const QUALITY = { draft: { size: [640, 360], samples: 16 }, good: { size: [1280, 720], samples: 32 }, high: { size: [1920, 1080], samples: 64 } } as const

interface GroomParams { lengthCm: number | null; density: number; comb: number; lift: number; gravity: number; frizz: number; melanin: number; redness: number }
const GROOM_DEFAULT: GroomParams = { lengthCm: null, density: 40000, comb: 0.6, lift: 0.8, gravity: 0.2, frizz: 0.3, melanin: 0.82, redness: 0.55 }

function Slider({ label, value, min, max, step, onChange, fmt }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; fmt?: (v: number) => string }) {
  return (
    <label style={{ display: 'grid', gridTemplateColumns: '92px 1fr 52px', alignItems: 'center', gap: 8, fontSize: 12, margin: '3px 0' }}>
      <span className="muted">{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <span style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmt ? fmt(value) : value}</span>
    </label>
  )
}

const SHELL_SLIDERS: [keyof FurParams, string, number, number, number, ((v: number) => string)?][] = [
  ['shells', 'Shells', 2, 48, 1],
  ['length', 'Length', 0.2, 3, 0.05, (v) => `${v.toFixed(2)}×`],
  ['strands', 'Strands', 60, 700, 10],
  ['thickness', 'Thickness', 0.05, 0.3, 0.01, (v) => v.toFixed(2)],
  ['comb', 'Comb', -1, 2, 0.05, (v) => v.toFixed(2)],
  ['gravity', 'Gravity', 0, 1.5, 0.05, (v) => v.toFixed(2)],
  ['wind', 'Wind', 0, 1, 0.02, (v) => v.toFixed(2)],
  ['inertia', 'Inertia', 0, 3, 0.1, (v) => v.toFixed(1)],
  ['occlusion', 'Root shadow', 0, 1, 0.05, (v) => v.toFixed(2)],
  ['sheen', 'Sheen', 0, 1, 0.05, (v) => v.toFixed(2)],
]

/** preferSource: "rig:<id>" / "export:<id>" preselects the character, "groom:<id>" opens an existing groom (3D Models library). */
export default function FurSection({ preferSource }: { preferSource?: string } = {}) {
  const [sources, setSources] = useState<FurSource[]>([])
  const [grooms, setGrooms] = useState<FurRecord[]>([])
  const [sourceId, setSourceId] = useState('')
  const [sel, setSel] = useState<string>('')
  const [gp, setGp] = useState<GroomParams>(GROOM_DEFAULT)
  const [shell, setShell] = useState<FurParams>(DEFAULT_FUR)
  const [furOn, setFurOn] = useState(true)
  const [stats, setStats] = useState('')
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState('')
  const [eng, setEng] = useState(false)
  const fur = useRef<ShellFur | null>(null)
  const shellRef = useRef(shell)
  shellRef.current = shell
  const job = useJob<FurRecord>()
  const vjob = useJob<VideoResult>()
  const [vcam, setVcam] = useState<'three_quarter' | 'side' | 'front'>('three_quarter')
  const [valpha, setValpha] = useState(false)
  const [vq, setVq] = useState<keyof typeof QUALITY>('good')

  const reload = useCallback(async () => {
    try {
      const [s, g] = await Promise.all([api.get<FurSource[]>('/api/anim/fur/sources'), api.get<FurRecord[]>('/api/anim/fur')])
      setSources(s); setGrooms(g); setErr('')
      setSourceId((cur) => cur || s.find((x) => x.animated)?.id || s[0]?.id || '')
      setSel((cur) => cur || g[0]?.id || '')
    } catch (e: any) { setErr(e.message || String(e)) }
  }, [])
  useEffect(() => { reload() }, [reload])
  useEffect(() => {
    if (!preferSource) return
    if (preferSource.startsWith('groom:')) setSel(preferSource.slice(6))
    else if (sources.some((s) => s.id === preferSource)) setSourceId(preferSource)
  }, [preferSource, sources])

  const rec = useMemo(() => grooms.find((g) => g.id === sel) || null, [grooms, sel])
  useEffect(() => { if (rec) setShell({ ...DEFAULT_FUR, ...rec.shell }) }, [rec])
  useEffect(() => { fur.current?.setParams(shell); setStats(statText(fur.current)) }, [shell])
  useEffect(() => { fur.current?.setVisible(furOn) }, [furOn])

  const startGroom = async () => {
    if (!sourceId) return
    setSaved('')
    const params: Record<string, any> = {
      density_per_m2: gp.density, comb: gp.comb, lift: gp.lift, gravity: gp.gravity, frizz: gp.frizz, melanin: gp.melanin, redness: gp.redness,
      length_m: gp.lengthCm ? gp.lengthCm / 100 : null,
    }
    try {
      const done = await job.run(() => api.post('/api/anim/fur', { source: sourceId, params, shell }))
      await reload()
      if (done.result?.id) setSel(done.result.id)
    } catch { /* shown in JobBar */ }
  }

  const saveShell = async () => {
    if (!rec) return
    try {
      const r = await api.put<FurRecord>(`/api/anim/fur/${rec.id}/shell`, { shell })
      setGrooms((gs) => gs.map((g) => (g.id === r.id ? r : g)))
      setSaved('Saved to fur.json')
    } catch (e: any) { setSaved(`Save failed: ${e.message}`) }
  }

  const renderVideo = async () => {
    if (!rec) return
    try {
      await vjob.run(() => api.post(`/api/anim/fur/${rec.id}/video`, { camera: vcam, transparent: valpha, ...QUALITY[vq] }))
      await reload()
    } catch { /* JobBar */ }
  }
  const toTimeline = async (mediaId: string) => {
    try { emit('cutlab:add-to-timeline', { media: await api.get<MediaRow>(`/api/media/${mediaId}`) }) } catch (e: any) { setSaved(`Could not add: ${e.message}`) }
  }

  const onModel = useCallback((root: import('three').Object3D) => {
    const f = addShellFur(root, shellRef.current)
    f.setVisible(furOnRef.current)
    fur.current = f
    setStats(statText(f))
    return {
      update: (dt: number, cam: import('three').Camera) => f.update(dt, cam),
      dispose: () => { f.dispose(); if (fur.current === f) fur.current = null },
    }
  }, [])
  const furOnRef = useRef(furOn)
  furOnRef.current = furOn

  const setS = <K extends keyof FurParams>(k: K, v: FurParams[K]) => setShell((s) => ({ ...s, [k]: v }))

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(300px, 360px) 1fr', gap: 14, padding: 14, overflow: 'auto', height: '100%', alignContent: 'start' }}>
      <div style={{ minWidth: 0 }}>
        <Section title="1 · Groom in Blender" right={<button className="btn ghost sm" onClick={reload} title="Refresh"><RefreshCw size={13} /></button>}>
          {err && <Note tone="warn">{err}</Note>}
          <label className="muted" style={{ fontSize: 12 }}>Character (skinned GLB)</label>
          <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} style={{ width: '100%', margin: '4px 0 8px' }}>
            {!sources.length && <option value="">No GLB exports yet - export a character from Retarget & Export</option>}
            {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <Slider label="Length" value={gp.lengthCm ?? 0} min={0} max={20} step={0.5} onChange={(v) => setGp({ ...gp, lengthCm: v || null })} fmt={(v) => (v ? `${v} cm` : 'auto')} />
          <Slider label="Density" value={gp.density} min={5000} max={120000} step={5000} onChange={(v) => setGp({ ...gp, density: v })} fmt={(v) => `${Math.round(v / 1000)}k/m²`} />
          <Slider label="Comb" value={gp.comb} min={0} max={1.5} step={0.05} onChange={(v) => setGp({ ...gp, comb: v })} fmt={(v) => v.toFixed(2)} />
          <Slider label="Lift" value={gp.lift} min={0} max={1.5} step={0.05} onChange={(v) => setGp({ ...gp, lift: v })} fmt={(v) => v.toFixed(2)} />
          <Slider label="Gravity" value={gp.gravity} min={0} max={1} step={0.05} onChange={(v) => setGp({ ...gp, gravity: v })} fmt={(v) => v.toFixed(2)} />
          <Slider label="Frizz" value={gp.frizz} min={0} max={1} step={0.05} onChange={(v) => setGp({ ...gp, frizz: v })} fmt={(v) => v.toFixed(2)} />
          <Slider label="Melanin" value={gp.melanin} min={0} max={1} step={0.02} onChange={(v) => setGp({ ...gp, melanin: v })} fmt={(v) => v.toFixed(2)} />
          <Slider label="Redness" value={gp.redness} min={0} max={1} step={0.02} onChange={(v) => setGp({ ...gp, redness: v })} fmt={(v) => v.toFixed(2)} />
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn primary" disabled={!sourceId || job.busy} onClick={startGroom}><Scissors size={14} />Groom + render</button>
            <button className="btn ghost sm" onClick={() => setGp(GROOM_DEFAULT)}>Reset</button>
          </div>
          <JobBar job={job.job} error={job.error} onCancel={job.cancel} />
          <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>
            Writes fur length, density and comb direction into the GLB, builds a strand groom (Geometry Nodes) and renders it in Cycles.
            Length "auto" is 5% of the character height; regions follow the bone names (short on paws and face, long on the neck and back).
          </div>
        </Section>
        <Section title="Grooms">
          {!grooms.length && <div className="faint" style={{ fontSize: 12 }}>None yet.</div>}
          {grooms.map((g) => (
            <button key={g.id} className={`btn ${g.id === sel ? 'primary' : 'ghost'}`} style={{ width: '100%', justifyContent: 'flex-start', marginBottom: 4 }} onClick={() => setSel(g.id)}>
              <span className="grow" style={{ textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.name}</span>
              <span style={{ fontSize: 11, opacity: 0.7 }}>{g.groom?.hair_count ? `${Math.round(g.groom.hair_count / 1000)}k strands` : ''}</span>
            </button>
          ))}
        </Section>
      </div>

      <div style={{ minWidth: 0 }}>
        {!rec ? <Note>Groom a character to see real-time shell fur and the Cycles strand render here.</Note> : (
          <>
            <Section title="2 · Real-time shell fur (three.js)" right={
              <label className="row" style={{ gap: 4, fontSize: 12, cursor: 'pointer' }}><input type="checkbox" checked={furOn} onChange={(e) => setFurOn(e.target.checked)} />Fur</label>}>
              <GlbViewer key={rec.id} url={rec.urls.glb} height={380} onModel={onModel} background={0x2a2f38} />
              <div className="faint" style={{ fontSize: 11, margin: '4px 0 8px' }}>{stats}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', columnGap: 18 }}>
                {SHELL_SLIDERS.map(([k, label, min, max, step, fmt]) => (
                  <Slider key={k} label={label} value={shell[k] as number} min={min} max={max} step={step} fmt={fmt} onChange={(v) => setS(k, v as never)} />
                ))}
                <label className="row" style={{ fontSize: 12, gap: 6 }}>
                  <input type="checkbox" checked={shell.lodDistance > 0} onChange={(e) => setS('lodDistance', e.target.checked ? 4 : 0)} />
                  Distance LOD (fewer shells far away)
                </label>
              </div>
              <div className="row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
                <button className="btn" onClick={saveShell}><Save size={14} />Save settings</button>
                <button className="btn ghost sm" onClick={() => setShell({ ...DEFAULT_FUR })}>Defaults</button>
                <span className="faint" style={{ fontSize: 12 }}>{saved}</span>
                <span className="grow" />
                <a className="btn ghost sm" href={rec.urls.glb} download><Download size={13} />Fur GLB</a>
                <a className="btn ghost sm" href={rec.urls.json} download><Download size={13} />fur.json</a>
                {rec.urls.blend && <a className="btn ghost sm" href={rec.urls.blend} download><Download size={13} />Groom .blend</a>}
                <button className="btn sm" onClick={() => setEng(true)}><Gamepad2 size={13} />Export to engine</button>
                {eng && <EngineExport asset={{ type: 'fur', groom_id: rec.id }} defaultName={rec.name} onClose={() => setEng(false)} />}
              </div>
            </Section>
            {rec.source.animated && (
              <Section title="4 · Fur video (Cycles) - for the video editor">
                <div className="row" style={{ flexWrap: 'wrap', gap: 8, fontSize: 12 }}>
                  <label className="row" style={{ gap: 4 }}>Camera
                    <select value={vcam} onChange={(e) => setVcam(e.target.value as typeof vcam)}>
                      <option value="three_quarter">Three-quarter</option><option value="side">Side</option><option value="front">Front</option>
                    </select>
                  </label>
                  <label className="row" style={{ gap: 4 }}>Quality
                    <select value={vq} onChange={(e) => setVq(e.target.value as keyof typeof QUALITY)}>
                      <option value="draft">Draft 640×360</option><option value="good">Good 1280×720</option><option value="high">High 1920×1080</option>
                    </select>
                  </label>
                  <label className="row" style={{ gap: 4, cursor: 'pointer' }}><input type="checkbox" checked={valpha} onChange={(e) => setValpha(e.target.checked)} />Transparent background (keeps the shadow)</label>
                  <button className="btn primary" disabled={vjob.busy} onClick={renderVideo}><Film size={14} />Render video</button>
                </div>
                <JobBar job={vjob.job} error={vjob.error} onCancel={vjob.cancel} />
                <div className="faint" style={{ fontSize: 11, margin: '4px 0 8px' }}>Renders every frame of the clip with strand fur and adds it to the Library. Transparent renders are WebM with alpha, ready to lay over footage.</div>
                {(rec.videos || []).map((v) => {
                  const name = v.file.split(/[\/]/).pop()!
                  return (
                    <div key={v.file} style={{ marginBottom: 10 }}>
                      <video src={`/api/anim/fur/${rec.id}/file/${encodeURIComponent(name)}`} controls loop muted playsInline
                        style={{ width: '100%', borderRadius: 6, display: 'block', background: v.transparent ? 'repeating-conic-gradient(#3a3f4a 0 25%, #2a2e36 0 50%) 0 0/16px 16px' : '#000' }} />
                      <div className="row" style={{ fontSize: 11, marginTop: 4 }}>
                        <span className="faint grow">{v.camera.replace('_', '-')}{v.transparent ? ' · alpha' : ''} · {v.frames} frames @ {Math.round(v.fps)} fps{v.seconds ? ` · rendered in ${Math.round(v.seconds)} s on ${v.device}` : ''}</span>
                        <button className="btn sm" onClick={() => toTimeline(v.media_id)}><Clapperboard size={13} />Add to timeline</button>
                      </div>
                    </div>
                  )
                })}
              </Section>
            )}
            {rec.urls.render && (
              <Section title="3 · Strand render (Blender Cycles) - for cutscenes and trailers">
                <img src={rec.urls.render} alt="Cycles fur render" style={{ width: '100%', borderRadius: 6, display: 'block' }} />
                <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
                  {rec.groom.hair_count?.toLocaleString()} strands · base length {(rec.groom.base_length_m * 100).toFixed(1)} cm{rec.groom.render_device ? ` · rendered on ${rec.groom.render_device}` : ''}.
                  The .blend keeps the groom attached to the armature, so any retargeted clip renders with fur.
                </div>
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function statText(f: ShellFur | null) {
  if (!f) return ''
  const s = f.stats()
  return `${s.meshes} mesh(es) × ${s.shells} shells = ${s.drawCalls} extra draw calls${s.synthesised ? ' · no groom data in this GLB, using uniform fur' : ''}`
}
