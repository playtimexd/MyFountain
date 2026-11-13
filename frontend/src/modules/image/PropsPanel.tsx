import { useEffect, useRef, useState } from 'react'
import { Check, ColorChip, Section, Seg, Slider } from './controls'
import type { Doc } from './doc'
import { ADJ_DEFS, adjDef, defaultParams } from './fx'
import { BUILTIN_FONTS, listFonts, ensureFont } from './render'
import { curDoc, focusReq, useUI } from './store'
import type { Layer, ShapeKind, ShapeProps, TextProps } from './types'
import { uid } from './util'

function NumField({ label, value, onChange, step = 1, w = 62 }: { label: string; value: number; onChange: (v: number) => void; step?: number; w?: number }) {
  const [txt, setTxt] = useState<string | null>(null)
  return (
    <label className="ie-num"><span>{label}</span>
      <input className="input" style={{ width: w }} type="number" step={step} value={txt ?? String(Math.round(value * 100) / 100)}
        onFocus={() => setTxt(String(Math.round(value * 100) / 100))} onBlur={() => setTxt(null)}
        onChange={(e) => { setTxt(e.target.value); const v = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(v)) onChange(v) }} />
    </label>
  )
}

function TextSection({ d, L }: { d: Doc; L: Layer }) {
  const t = L.text!
  const [fonts, setFonts] = useState<string[]>(BUILTIN_FONTS)
  const ta = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { void listFonts().then((l) => setFonts([...new Set([...BUILTIN_FONTS, ...l.map((f) => f.family)])])) }, [])
  useEffect(() => {
    const go = () => setTimeout(() => { ta.current?.focus(); ta.current?.select(); focusReq.text = false }, 30)
    if (focusReq.text) go()
    const h = () => go()
    window.addEventListener('ie-focus-text', h)
    return () => window.removeEventListener('ie-focus-text', h)
  }, [])
  const set = (patch: Partial<TextProps>, key = 'text') => d.mutate(L, 'Edit text', (l) => {
    Object.assign(l.text!, patch)
    if (patch.text !== undefined) l.name = (patch.text.split('\n')[0] || 'Text').slice(0, 16)
  }, key)
  return (
    <Section title="Text">
      <textarea ref={ta} className="textarea" rows={3} value={t.text} onChange={(e) => set({ text: e.target.value }, 'text-content')} />
      <div className="row" style={{ margin: '6px 0' }}>
        <select className="select sm grow" value={t.font} onChange={(e) => { const f = e.target.value; void ensureFont(f).then(() => set({ font: f }, 'font')) }}>
          {!fonts.includes(t.font) && <option>{t.font}</option>}
          {fonts.map((f) => <option key={f} value={f} style={{ fontFamily: f }}>{f}</option>)}
        </select>
        <ColorChip value={t.color} onChange={(c) => set({ color: c }, 'tcolor')} />
      </div>
      <Slider label="Size" value={Math.round(t.size)} min={4} max={500} onChange={(v) => set({ size: v }, 'tsize')} unit="px" />
      <div className="row" style={{ margin: '4px 0' }}>
        <button className={`btn sm ${t.bold ? 'primary' : ''}`} onClick={() => set({ bold: !t.bold })}><b>B</b></button>
        <button className={`btn sm ${t.italic ? 'primary' : ''}`} onClick={() => set({ italic: !t.italic })}><i>I</i></button>
        <button className={`btn sm ${t.underline ? 'primary' : ''}`} onClick={() => set({ underline: !t.underline })}><u>U</u></button>
        <Seg value={t.align} onChange={(v) => set({ align: v })} options={[['left', 'Left'], ['center', 'Center'], ['right', 'Right']]} />
      </div>
      <Slider label="Tracking" value={t.letterSpacing} min={-10} max={60} step={0.5} onChange={(v) => set({ letterSpacing: v }, 'ls')} />
      <Slider label="Leading" value={t.lineHeight} min={0.8} max={3} step={0.05} onChange={(v) => set({ lineHeight: v }, 'lh')} />
      <div className="row" style={{ margin: '6px 0' }}>
        <span className="muted">Stroke</span><ColorChip value={t.strokeColor} onChange={(c) => set({ strokeColor: c }, 'tsc')} />
        <div className="grow"><Slider label="Width" value={t.strokeWidth} min={0} max={40} step={0.5} onChange={(v) => set({ strokeWidth: v }, 'tsw')} /></div>
      </div>
      <div className="row"><Check label="Shadow" checked={t.shadowOn} onChange={(v) => set({ shadowOn: v })} />
        <ColorChip value={t.shadowColor} onChange={(c) => set({ shadowColor: c }, 'tshc')} /></div>
      {t.shadowOn && (<>
        <Slider label="Blur" value={t.shadowBlur} min={0} max={60} onChange={(v) => set({ shadowBlur: v }, 'tshb')} />
        <Slider label="Offset X" value={t.shadowX} min={-60} max={60} onChange={(v) => set({ shadowX: v }, 'tshx')} />
        <Slider label="Offset Y" value={t.shadowY} min={-60} max={60} onChange={(v) => set({ shadowY: v }, 'tshy')} />
      </>)}
      <div className="row" style={{ marginTop: 4 }}><Check label="Background box" checked={t.bgOn} onChange={(v) => set({ bgOn: v })} />
        <ColorChip value={t.bgColor} onChange={(c) => set({ bgColor: c }, 'tbg')} /></div>
      {t.bgOn && <Slider label="Padding" value={t.bgPad} min={0} max={80} onChange={(v) => set({ bgPad: v }, 'tbp')} />}
    </Section>
  )
}

function ShapeSection({ d, L }: { d: Doc; L: Layer }) {
  const s = L.shape!
  const set = (patch: Partial<ShapeProps>, key = 'shape') => d.mutate(L, 'Edit shape', (l) => { Object.assign(l.shape!, patch) }, key)
  return (
    <Section title="Shape">
      <Seg<ShapeKind> value={s.kind} onChange={(v) => set({ kind: v })} options={[['rect', 'Rectangle'], ['ellipse', 'Ellipse'], ['line', 'Line'], ['polygon', 'Polygon'], ['star', 'Star']]} />
      <div className="row" style={{ margin: '6px 0' }}><NumField label="W" value={s.w} onChange={(v) => set({ w: Math.max(1, v) }, 'sw')} /><NumField label="H" value={s.h} onChange={(v) => set({ h: Math.max(1, v) }, 'sh')} /></div>
      <div className="row"><Check label="Fill" checked={s.fillOn} onChange={(v) => set({ fillOn: v })} /><ColorChip value={s.fill} onChange={(c) => set({ fill: c }, 'sf')} /></div>
      <div className="row" style={{ margin: '4px 0' }}><Check label="Stroke" checked={s.strokeOn} onChange={(v) => set({ strokeOn: v })} /><ColorChip value={s.stroke} onChange={(c) => set({ stroke: c }, 'ss')} /></div>
      {s.strokeOn && <Slider label="Stroke width" value={s.strokeWidth} min={1} max={80} onChange={(v) => set({ strokeWidth: v }, 'ssw')} />}
      {s.kind === 'rect' && <Slider label="Corner radius" value={s.radius} min={0} max={300} onChange={(v) => set({ radius: v }, 'sr')} />}
      {(s.kind === 'polygon' || s.kind === 'star') && <Slider label="Sides / points" value={s.sides} min={3} max={20} onChange={(v) => set({ sides: v }, 'sn')} />}
    </Section>
  )
}

function TransformSection({ d, L }: { d: Doc; L: Layer }) {
  const w = Math.abs(L.canvas.width * L.t.sx), h = Math.abs(L.canvas.height * L.t.sy)
  const set = (fn: (l: Layer) => void) => { if (L.locked) return; d.mutate(L, 'Transform', fn, 'xf') }
  return (
    <Section title="Transform" defaultOpen={false}>
      <div className="row wrap">
        <NumField label="X" value={L.t.cx} onChange={(v) => set((l) => { l.t.cx = v })} />
        <NumField label="Y" value={L.t.cy} onChange={(v) => set((l) => { l.t.cy = v })} />
        <NumField label="W" value={w} onChange={(v) => set((l) => { l.t.sx = (Math.sign(l.t.sx) || 1) * Math.max(0.01, v) / l.canvas.width })} />
        <NumField label="H" value={h} onChange={(v) => set((l) => { l.t.sy = (Math.sign(l.t.sy) || 1) * Math.max(0.01, v) / l.canvas.height })} />
        <NumField label="Rotate°" value={(L.t.rot * 180) / Math.PI} onChange={(v) => set((l) => { l.t.rot = (v * Math.PI) / 180 })} />
      </div>
      <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>X/Y are the layer center coordinates.</div>
    </Section>
  )
}

function AdjustSection({ d, L }: { d: Doc; L: Layer }) {
  const [add, setAdd] = useState('')
  return (
    <Section title="Adjustments (non-destructive)" right={
      <select className="select sm" value={add} onChange={(e) => {
        const type = e.target.value
        setAdd('')
        if (!type) return
        d.mutate(L, 'Add adjustment', (l) => { l.adjust.push({ id: uid(), type, enabled: true, p: defaultParams(adjDef(type).params) }) })
      }}>
        <option value="">+ Add…</option>
        {ADJ_DEFS.map((a) => <option key={a.type} value={a.type}>{a.label}</option>)}
      </select>}>
      {!L.adjust.length && <div className="faint" style={{ fontSize: 12 }}>No adjustments yet. Stack as many as you like and tweak or disable them any time.</div>}
      {L.adjust.map((a, i) => {
        const def = adjDef(a.type)
        return (
          <div key={a.id} className="ie-adj">
            <div className="row">
              <input type="checkbox" checked={a.enabled} onChange={() => d.mutate(L, 'Toggle adjustment', (l) => { l.adjust[i].enabled = !l.adjust[i].enabled })} />
              <b className="grow">{def.label}</b>
              <button className="btn ghost sm" title="Reset" onClick={() => d.mutate(L, 'Reset adjustment', (l) => { l.adjust[i].p = defaultParams(def.params) })}>↺</button>
              <button className="btn ghost sm danger" title="Delete" onClick={() => d.mutate(L, 'Delete adjustment', (l) => { l.adjust.splice(i, 1) })}>✕</button>
            </div>
            {def.params.map((p) => (
              <Slider key={p.key} label={p.label} value={a.p[p.key] ?? p.def} min={p.min} max={p.max} step={p.step ?? 1}
                onChange={(v) => d.mutate(L, def.label, (l) => { l.adjust[i].p[p.key] = v }, 'adj' + a.id + p.key)} />
            ))}
          </div>
        )
      })}
      {L.adjust.length > 0 && <button className="btn sm" style={{ marginTop: 6 }} onClick={() => d.bakeAll(L)}>Merge Adjustments into Pixels</button>}
    </Section>
  )
}
function FxSection({ d, L }: { d: Doc; L: Layer }) {
  const f = L.fx
  const set = (fn: (fx: Layer['fx']) => void, key: string) => d.mutate(L, 'Layer style', (l) => fn(l.fx), key)
  return (
    <Section title="Layer Style" defaultOpen={false}>
      <div className="row"><Check label="Drop Shadow" checked={f.shadow.on} onChange={(v) => set((x) => { x.shadow.on = v }, 'fx')} />
        <ColorChip value={f.shadow.color} onChange={(c) => set((x) => { x.shadow.color = c }, 'fxc1')} /></div>
      {f.shadow.on && (<>
        <Slider label="Opacity" value={Math.round(f.shadow.opacity * 100)} min={0} max={100} onChange={(v) => set((x) => { x.shadow.opacity = v / 100 }, 'fxo1')} unit="%" />
        <Slider label="Distance X" value={f.shadow.dx} min={-100} max={100} onChange={(v) => set((x) => { x.shadow.dx = v }, 'fxx')} />
        <Slider label="Distance Y" value={f.shadow.dy} min={-100} max={100} onChange={(v) => set((x) => { x.shadow.dy = v }, 'fxy')} />
        <Slider label="Blur" value={f.shadow.blur} min={0} max={100} onChange={(v) => set((x) => { x.shadow.blur = v }, 'fxb')} />
      </>)}
      <div className="row" style={{ marginTop: 4 }}><Check label="Stroke" checked={f.stroke.on} onChange={(v) => set((x) => { x.stroke.on = v }, 'fx')} />
        <ColorChip value={f.stroke.color} onChange={(c) => set((x) => { x.stroke.color = c }, 'fxc2')} /></div>
      {f.stroke.on && <Slider label="Width" value={f.stroke.width} min={1} max={60} onChange={(v) => set((x) => { x.stroke.width = v }, 'fxw')} />}
      <div className="row" style={{ marginTop: 4 }}><Check label="Outer Glow" checked={f.glow.on} onChange={(v) => set((x) => { x.glow.on = v }, 'fx')} />
        <ColorChip value={f.glow.color} onChange={(c) => set((x) => { x.glow.color = c }, 'fxc3')} /></div>
      {f.glow.on && (<>
        <Slider label="Size" value={f.glow.size} min={1} max={80} onChange={(v) => set((x) => { x.glow.size = v }, 'fxs')} />
        <Slider label="Opacity" value={Math.round(f.glow.opacity * 100)} min={0} max={100} onChange={(v) => set((x) => { x.glow.opacity = v / 100 }, 'fxo3')} unit="%" />
      </>)}
    </Section>
  )
}

export function PropsPanel() {
  useUI((s) => s.rev)
  const d = curDoc()
  const L = d?.active
  if (!d || !L) return <div className="faint pad">Select a layer to edit its properties</div>
  return (
    <div className="ie-props">
      <div className="row" style={{ padding: '2px 0 6px' }}><b className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{L.name}</b>
        {L.mask && <>
          <Check label="Mask" checked={L.mask.enabled} onChange={() => d.mutate(L, 'Toggle layer mask', (l) => { l.mask!.enabled = !l.mask!.enabled })} />
          <button className="btn sm" onClick={() => d.invertMask(L)}>Invert</button>
          <button className="btn sm" onClick={() => d.removeMask(L, true)}>Apply</button>
        </>}
        {L.type !== 'raster' && L.type !== 'group' && <button className="btn sm" title="Convert to a normal layer (text/shape can no longer be edited)" onClick={() => { d.bake(L); d.changed() }}>Rasterize</button>}
      </div>
      {L.type === 'text' && L.text && <TextSection d={d} L={L} />}
      {L.type === 'shape' && L.shape && <ShapeSection d={d} L={L} />}
      {L.type !== 'group' && <TransformSection d={d} L={L} />}
      <AdjustSection d={d} L={L} />
      <FxSection d={d} L={L} />
    </div>
  )
}
