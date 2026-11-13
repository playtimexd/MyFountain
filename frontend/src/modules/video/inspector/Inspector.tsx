import { useState } from 'react'
import { Eraser, Film, FlipHorizontal, FlipVertical, MousePointerClick, Plus, RotateCcw, Scissors, Subtitles, Trash2, Wand2 } from 'lucide-react'
import { useProject, selectedClips } from '../store'
import { useCatalog, TRANSITION_LABEL } from '../data'
import { Section, Slider, Toggle } from '../ui'
import { DEFAULT_ADJUST, DEFAULT_TRANSFORM, clipDuration } from '../../../types/project'
import type { Clip, TransitionIn } from '../../../types/project'
import { interpKeyframes } from '../ops'
import { clock } from '../engine/clock'
import { clipTraits } from '../clipTypes'
import { Shortcuts } from '../../../shared/ui'
import { AI_TOOLS, useAiFocus } from '../../ai/focus'
import type { AiToolId } from '../../ai/focus'

const ADJ: { k: keyof typeof DEFAULT_ADJUST; label: string; min: number; max: number; step: number; unit?: string }[] = [
  { k: 'brightness', label: 'Brightness', min: -1, max: 1, step: 0.01 },
  { k: 'contrast', label: 'Contrast', min: 0, max: 2, step: 0.01 },
  { k: 'saturation', label: 'Saturation', min: 0, max: 3, step: 0.01 },
  { k: 'hue', label: 'Hue', min: -180, max: 180, step: 1, unit: '°' },
  { k: 'temperature', label: 'Temperature', min: -1, max: 1, step: 0.01 },
  { k: 'sharpen', label: 'Sharpen', min: 0, max: 1, step: 0.01 },
  { k: 'vignette', label: 'Vignette', min: 0, max: 1, step: 0.01 },
  { k: 'blur', label: 'Blur', min: 0, max: 40, step: 0.5, unit: 'px' },
  { k: 'sepia', label: 'Sepia', min: 0, max: 1, step: 0.01 },
  { k: 'grayscale', label: 'Grayscale', min: 0, max: 1, step: 0.01 },
]

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="ve-row"><span className="ve-row-l">{label}</span><div className="ve-row-c">{children}</div></div>
}

function splitRgba(c: string): { rgb: string; a: number } {
  if (!c) return { rgb: '#000000', a: 0 }
  const m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(c)
  if (m) return { rgb: '#' + m[1], a: m[2] ? parseInt(m[2], 16) / 255 : 1 }
  return { rgb: '#000000', a: 0.7 }
}
const joinRgba = (rgb: string, a: number) => (a <= 0 ? '' : rgb + Math.round(Math.min(1, a) * 255).toString(16).padStart(2, '0'))

export default function Inspector() {
  const project = useProject((s) => s.project)
  const selection = useProject((s) => s.selection)
  const sel = selectedClips({ project, selection })
  if (!sel.length) return <ProjectSettings />
  return <ClipInspector clips={sel} ids={sel.map((c) => c.id)} />
}

function ProjectSettings() {
  const project = useProject((s) => s.project)
  const mutate = useProject((s) => s.mutate)
  const setCanvas = useProject((s) => s.setCanvas)
  return (
    <div className="ve-insp">
      <div className="ve-insp-title">Project settings</div>
      <Section title="General">
        <Row label="Name"><input className="input" value={project.name} onChange={(e) => mutate((p) => ({ ...p, name: e.target.value }), 'name')} /></Row>
        <Row label="Canvas"><div className="row">
          <input className="input" type="number" value={project.width} onChange={(e) => setCanvas(+e.target.value || project.width, project.height)} />
          <span>×</span>
          <input className="input" type="number" value={project.height} onChange={(e) => setCanvas(project.width, +e.target.value || project.height)} />
        </div></Row>
        <Row label="Frame rate"><select className="select" value={project.fps} onChange={(e) => mutate((p) => ({ ...p, fps: +e.target.value }))}>
          {[24, 25, 30, 50, 60].map((f) => <option key={f} value={f}>{f} fps</option>)}
        </select></Row>
        <Row label="Background"><input type="color" className="ve-color" value={/^#[0-9a-f]{6}$/i.test(project.background) ? project.background : '#000000'}
          onChange={(e) => mutate((p) => ({ ...p, background: e.target.value }), 'bg')} /></Row>
      </Section>
      <div className="ve-insp-hint">
        <p className="row" style={{ gap: 6 }}><MousePointerClick size={14} />Select a clip to edit it. Right-click a clip for AI tools.</p>
        <div className="ui-section-label">Shortcuts</div>
        <Shortcuts items={SHORTCUTS} />
      </div>
    </div>
  )
}

const SHORTCUTS: [string[], string][] = [
  [['Space'], 'Play / pause'], [['S'], 'Split at playhead'], [['Del'], 'Delete'], [['Shift', 'Del'], 'Ripple delete'],
  [['Ctrl', 'Z'], 'Undo'], [['Ctrl', 'Y'], 'Redo'], [['Ctrl', 'D'], 'Duplicate'], [['Ctrl', 'Shift', 'S'], 'Split audio'], [['Ctrl', 'A'], 'Select all'],
  [['←', '→'], 'Step one frame'], [['Home', 'End'], 'Go to start / end'], [['+', '−'], 'Timeline zoom'], [['Esc'], 'Clear selection'],
]

const AI_ICON: Record<AiToolId, typeof Film> = { upscale: Film, watermark: Eraser, removebg: Wand2, subtitles: Subtitles, smartcut: Scissors }

/** One-click entry into the AI panel for the selected clip's media. */
export function AiActions({ clip }: { clip: Clip }) {
  const kind = clip.type === 'sticker' ? 'image' : clip.type
  const tools = AI_TOOLS.filter((t) => (t.kinds as string[]).includes(kind))
  if (!clip.assetId || !tools.length) return null
  return (
    <div className="ve-ai-actions">
      {tools.map((t) => {
        const I = AI_ICON[t.id]
        return <button key={t.id} className="btn sm" onClick={() => useAiFocus.getState().open(t.id, clip.assetId || null)}><I size={13} />{t.label}</button>
      })}
    </div>
  )
}

function ClipInspector({ clips, ids }: { clips: Clip[]; ids: string[] }) {
  const c = clips[0]
  const st = useProject.getState
  const catalog = useCatalog((s) => s.catalog)
  const traits = clipTraits(c.type)
  const isVisual = traits.visual
  const isMedia = traits.timedSource
  const dur = clipDuration(c)
  const setT = (patch: Partial<Clip['transform']>, key?: string) => st().setTransform(ids, patch, key)
  const setA = (patch: Partial<Clip['adjust']>, key?: string) => st().setAdjust(ids, patch, key)
  const setC = (patch: Partial<Clip>, key?: string) => st().setClipField(ids, patch, key)
  const T = c.transform
  const tx = c.text
  const [kfMsg, setKfMsg] = useState('')

  const addKf = () => {
    const lt = Math.max(0, clock.time - c.start)
    const cur = interpKeyframes(c, lt)
    const ks = [...(c.keyframes || []).filter((k) => Math.abs(k.t - lt) > 0.02), { t: +lt.toFixed(3), ...cur }].sort((a, b) => a.t - b.t)
    setC({ keyframes: ks })
    setKfMsg(`Keyframe added at ${lt.toFixed(2)}s`)
  }

  return (
    <div className="ve-insp">
      <div className="ve-insp-title">
        <span className="grow">{clips.length > 1 ? `${clips.length} clips selected` : c.name || c.type}</span>
        <span className="chip">{{ video: 'Video', image: 'Image', audio: 'Audio', text: 'Text', sticker: 'Sticker' }[c.type]}</span>
      </div>

      {clips.length === 1 && c.assetId && (c.type === 'video' || c.type === 'image' || c.type === 'audio') && (
        <Section title="AI tools"><AiActions clip={c} /></Section>
      )}

      <Section title="Timing" defaultOpen={false}>
        <Row label="Start"><input className="input" type="number" step={0.1} value={+c.start.toFixed(3)} onChange={(e) => setC({ start: Math.max(0, +e.target.value || 0) }, 'start')} /></Row>
        <Row label="Duration"><span className="muted">{dur.toFixed(2)} s</span></Row>
        {isMedia && <Row label="Source"><span className="muted">{c.in.toFixed(2)} – {c.out.toFixed(2)} s</span></Row>}
      </Section>

      {isVisual && (
        <Section title="Transform" right={<button className="ve-reset" title="Reset" onClick={() => setT({ ...DEFAULT_TRANSFORM, flipX: false, flipY: false })}><RotateCcw size={12} /></button>}>
          <Slider label="Position X" value={T.x} min={-1} max={1} step={0.005} def={0} onChange={(v, k) => setT({ x: v }, k)} />
          <Slider label="Position Y" value={T.y} min={-1} max={1} step={0.005} def={0} onChange={(v, k) => setT({ y: v }, k)} />
          <Slider label="Scale" value={T.scale} min={0.05} max={4} step={0.01} def={1} onChange={(v, k) => setT({ scale: v }, k)} />
          <Slider label="Rotation" value={T.rotation} min={-180} max={180} step={0.5} def={0} unit="°" onChange={(v, k) => setT({ rotation: v }, k)} />
          <Slider label="Opacity" value={T.opacity} min={0} max={1} step={0.01} def={1} onChange={(v, k) => setT({ opacity: v }, k)} />
          <div className="row" style={{ marginTop: 4 }}>
            <Toggle on={!!T.flipX} onClick={() => setT({ flipX: !T.flipX })} title="Flip horizontally"><FlipHorizontal size={14} />Flip H</Toggle>
            <Toggle on={!!T.flipY} onClick={() => setT({ flipY: !T.flipY })} title="Flip vertically"><FlipVertical size={14} />Flip V</Toggle>
            {traits.transformFit && (
              <select className="select" style={{ width: 90 }} value={c.fit} onChange={(e) => setC({ fit: e.target.value as Clip['fit'] })}>
                <option value="contain">Fit</option><option value="cover">Fill</option>
              </select>
            )}
          </div>
        </Section>
      )}

      {c.type === 'text' && tx && (
        <Section title="Text style">
          <textarea className="textarea" rows={2} value={tx.content} onChange={(e) => st().setText(ids, { content: e.target.value }, 'txt')} />
          <Row label="Font">
            <select className="select" value={tx.font} onChange={(e) => st().setText(ids, { font: e.target.value })}>
              {!catalog.fonts.some((f) => f.family === tx.font) && <option value={tx.font}>{tx.font}</option>}
              {catalog.fonts.map((f) => <option key={f.family} value={f.family} style={{ fontFamily: f.family }}>{f.family}</option>)}
            </select>
          </Row>
          <Slider label="Size" value={tx.size} min={0.02} max={0.3} step={0.005} def={0.07} onChange={(v, k) => st().setText(ids, { size: v }, k)} />
          <Row label="Color">
            <div className="row">
              <input type="color" className="ve-color" value={tx.color} onChange={(e) => st().setText(ids, { color: e.target.value }, 'color')} />
              <Toggle on={tx.bold} onClick={() => st().setText(ids, { bold: !tx.bold })}><b>B</b></Toggle>
              <Toggle on={tx.italic} onClick={() => st().setText(ids, { italic: !tx.italic })}><i>I</i></Toggle>
              {(['left', 'center', 'right'] as const).map((a) => (
                <Toggle key={a} on={tx.align === a} onClick={() => st().setText(ids, { align: a })}>{{ left: 'L', center: 'C', right: 'R' }[a]}</Toggle>
              ))}
            </div>
          </Row>
          <Row label="Outline">
            <div className="row">
              <input type="color" className="ve-color" value={tx.stroke || '#000000'} onChange={(e) => st().setText(ids, { stroke: e.target.value }, 'stroke')} />
              <input className="ve-range grow" type="range" min={0} max={0.2} step={0.005} value={tx.strokeWidth}
                style={{ ['--p' as any]: `${(tx.strokeWidth / 0.2) * 100}%` }} onChange={(e) => st().setText(ids, { strokeWidth: +e.target.value }, 'sw')} />
            </div>
          </Row>
          <Row label="Shadow"><Toggle on={tx.shadow} onClick={() => st().setText(ids, { shadow: !tx.shadow })}>{tx.shadow ? 'On' : 'Off'}</Toggle></Row>
          <BgRow bg={tx.bg} onChange={(bg) => st().setText(ids, { bg }, 'bg')} />
          {tx.bg && <Slider label="BG padding" value={tx.bgPadding} min={0} max={1} step={0.01} def={0.25} onChange={(v, k) => st().setText(ids, { bgPadding: v }, k)} />}
          <Slider label="Letter spacing" value={tx.letterSpacing ?? 0} min={-0.1} max={0.6} step={0.01} def={0} onChange={(v, k) => st().setText(ids, { letterSpacing: v }, k)} />
        </Section>
      )}

      {isVisual && traits.adjustable && (
        <Section title="Adjust" defaultOpen={c.type === 'video' || c.type === 'image'}
          right={<button className="ve-reset" title="Reset" onClick={() => st().patchClips(ids, () => ({ adjust: { ...DEFAULT_ADJUST }, filter: undefined }))}><RotateCcw size={12} /></button>}>
          {ADJ.map((a) => (
            <Slider key={a.k} label={a.label} value={c.adjust[a.k]} min={a.min} max={a.max} step={a.step} def={DEFAULT_ADJUST[a.k]} unit={a.unit}
              onChange={(v, k) => setA({ [a.k]: v }, k)} />
          ))}
        </Section>
      )}

      {isVisual && traits.adjustable && (
        <Section title="Filter" defaultOpen={false}>
          <div className="ve-chips">
            <button className={`ve-chipbtn ${!c.filter ? 'on' : ''}`} onClick={() => st().patchClips(ids, () => ({ filter: undefined, adjust: { ...DEFAULT_ADJUST } }))}>None</button>
            {catalog.filters.map((f) => (
              <button key={f.id} className={`ve-chipbtn ${c.filter === f.id ? 'on' : ''}`}
                onClick={() => st().patchClips(ids, () => ({ filter: f.id, adjust: { ...DEFAULT_ADJUST, ...f.adjust } }))}>{f.name}</button>
            ))}
          </div>
        </Section>
      )}

      {isMedia && (
        <Section title="Speed">
          <Slider label="Speed" value={c.speed} min={0.1} max={8} step={0.05} def={1} unit="x" onChange={(v, k) => setC({ speed: Math.min(8, Math.max(0.1, v)) }, k)} />
          <div className="ve-chips">
            {[0.25, 0.5, 1, 1.5, 2, 4].map((s) => <button key={s} className={`ve-chipbtn ${c.speed === s ? 'on' : ''}`} onClick={() => setC({ speed: s })}>{s}x</button>)}
          </div>
        </Section>
      )}

      {isMedia && c.audioDetached && (
        <Section title="Volume">
          <div className="faint" style={{ fontSize: 11 }}>This clip's audio was split onto its own clip on an audio track; the video plays silent.</div>
          <div className="ve-chips"><button className="ve-chipbtn" onClick={() => setC({ audioDetached: false })}>Restore clip audio</button></div>
        </Section>
      )}
      {isMedia && !c.audioDetached && (
        <Section title="Volume">
          <Slider label="Volume" value={c.volume} min={0} max={2} step={0.01} def={1} onChange={(v, k) => setC({ volume: v }, k)} />
          <Slider label="Fade in" value={c.fadeIn} min={0} max={Math.max(0.5, Math.min(10, dur))} step={0.05} def={0} unit="s" onChange={(v, k) => setC({ fadeIn: v }, k)} />
          <Slider label="Fade out" value={c.fadeOut} min={0} max={Math.max(0.5, Math.min(10, dur))} step={0.05} def={0} unit="s" onChange={(v, k) => setC({ fadeOut: v }, k)} />
          <div className="faint" style={{ fontSize: 11 }}>Above 100% the preview amplifies through a WebAudio gain.</div>
        </Section>
      )}
      {!isMedia && (
        <Section title="Fades" defaultOpen={false}>
          <Slider label="Fade in" value={c.fadeIn} min={0} max={Math.max(0.5, Math.min(10, dur))} step={0.05} def={0} unit="s" onChange={(v, k) => setC({ fadeIn: v }, k)} />
          <Slider label="Fade out" value={c.fadeOut} min={0} max={Math.max(0.5, Math.min(10, dur))} step={0.05} def={0} unit="s" onChange={(v, k) => setC({ fadeOut: v }, k)} />
        </Section>
      )}

      {isVisual && (
        <Section title="Transition in" defaultOpen={false}>
          <Row label="Type">
            <select className="select" value={c.transitionIn?.type ?? ''} onChange={(e) => {
              const v = e.target.value
              st().setTransition(ids, v ? { type: v as TransitionIn['type'], duration: c.transitionIn?.duration ?? 0.6 } : undefined)
            }}>
              <option value="">None</option>
              {catalog.transitions.map((t) => <option key={t.id} value={t.id}>{TRANSITION_LABEL[t.id] || t.name}</option>)}
            </select>
          </Row>
          {c.transitionIn && (
            <Slider label="Duration" value={c.transitionIn.duration} min={0.1} max={Math.min(3, dur)} step={0.05} def={0.6} unit="s"
              onChange={(v, k) => st().patchClips(ids, (x) => (x.transitionIn ? { transitionIn: { ...x.transitionIn, duration: v } } : {}), k)} />
          )}
        </Section>
      )}

      {isVisual && (
        <Section title="Keyframes" defaultOpen={false} right={<button className="ve-reset" title="Add a keyframe at the playhead" onClick={addKf}><Plus size={13} /></button>}>
          <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>Records the current position/scale/rotation/opacity as a keyframe at the playhead; values are interpolated linearly in between.</div>
          {(c.keyframes || []).map((k, i) => (
            <div key={i} className="row ve-kf">
              <span className="chip">{k.t.toFixed(2)}s</span>
              <span className="grow faint" style={{ fontSize: 11 }}>x{(k.x ?? 0).toFixed(2)} y{(k.y ?? 0).toFixed(2)} ×{(k.scale ?? 1).toFixed(2)}</span>
              <button className="ve-reset" onClick={() => setC({ keyframes: (c.keyframes || []).filter((_, j) => j !== i) })}><Trash2 size={12} /></button>
            </div>
          ))}
          {kfMsg && <div className="faint" style={{ fontSize: 11 }}>{kfMsg}</div>}
        </Section>
      )}
    </div>
  )
}

function BgRow({ bg, onChange }: { bg: string; onChange: (bg: string) => void }) {
  const { rgb, a } = splitRgba(bg)
  return (
    <Row label="Background">
      <div className="row">
        <input type="color" className="ve-color" value={rgb} onChange={(e) => onChange(joinRgba(e.target.value, a || 0.8))} />
        <input className="ve-range grow" type="range" min={0} max={1} step={0.02} value={a} style={{ ['--p' as any]: `${a * 100}%` }}
          onChange={(e) => onChange(joinRgba(rgb, +e.target.value))} />
        <span className="faint" style={{ width: 30, fontSize: 11 }}>{bg ? `${Math.round(a * 100)}%` : 'None'}</span>
      </div>
    </Row>
  )
}
