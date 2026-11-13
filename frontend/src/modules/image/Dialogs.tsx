import { X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, ColorChip, Seg, Slider } from './controls'
import { PixelEdit, type Doc } from './doc'
import { defaultParams, findFilter } from './fx'
import { copyToClipboard, exportFile, saveProject, type ExportFmt } from './io'
import { fillSel, newDocument } from './ops'
import { closeDoc, curDoc, openDialog, useUI } from './store'
import { clipRect, ctx2, mkCanvas } from './util'

function Modal({ title, children, onClose, width = 380, footer }: { title: string; children: ReactNode; onClose: () => void; width?: number; footer?: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', h, true)
    return () => window.removeEventListener('keydown', h, true)
  }, [onClose])
  return (
    <div className="ie-modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ie-modal" style={{ width }} role="dialog">
        <div className="mh"><b className="grow">{title}</b><button className="btn ghost icon sm" onClick={onClose}><X size={14} /></button></div>
        <div className="mb">{children}</div>
        {footer && <div className="mf">{footer}</div>}
      </div>
    </div>
  )
}
const NumIn = ({ label, value, onChange, min = 1, max = 20000 }: { label: string; value: number; onChange: (v: number) => void; min?: number; max?: number }) => (
  <label className="ie-num"><span>{label}</span><input className="input" type="number" value={value} min={min} max={max} onChange={(e) => onChange(Math.max(min, Math.min(max, Math.round(Number(e.target.value) || min))))} /></label>
)

// ------------------------------------------------------------------ new doc
const PRESETS: [string, number, number][] = [['1080p 1920×1080', 1920, 1080], ['4K 3840×2160', 3840, 2160], ['1:1 1080×1080', 1080, 1080], ['9:16 1080×1920', 1080, 1920], ['4:5 1080×1350', 1080, 1350], ['A4 (300dpi) 2480×3508', 2480, 3508], ['720p 1280×720', 1280, 720]]
function NewDoc() {
  const [w, setW] = useState(1920), [h, setH] = useState(1080)
  const [bg, setBg] = useState<'white' | 'transparent' | 'color'>('white')
  const [color, setColor] = useState('#202124')
  const [name, setName] = useState('Untitled')
  const close = () => openDialog(null)
  return (
    <Modal title="New Document" onClose={close} footer={<><button className="btn" onClick={close}>Cancel</button>
      <button className="btn primary" onClick={() => { newDocument(w, h, bg === 'color' ? color : bg, name || 'Untitled'); close() }}>Create</button></>}>
      <div className="ie-presets">{PRESETS.map(([l, pw, ph]) => <button key={l} className={`btn sm ${w === pw && h === ph ? 'primary' : ''}`} onClick={() => { setW(pw); setH(ph) }}>{l}</button>)}</div>
      <div className="row" style={{ margin: '10px 0' }}><NumIn label="Width px" value={w} onChange={setW} max={8192} /><NumIn label="Height px" value={h} onChange={setH} max={8192} /></div>
      <div className="field"><label>Name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
      <div className="field"><label>Background</label>
        <div className="row"><Seg value={bg} onChange={setBg} options={[['white', 'White'], ['transparent', 'Transparent'], ['color', 'Color']]} />{bg === 'color' && <ColorChip value={color} onChange={setColor} alpha={false} />}</div></div>
    </Modal>
  )
}

// ------------------------------------------------------------------ export
function Export() {
  const d = curDoc()!
  const [fmt, setFmt] = useState<ExportFmt>('png')
  const [q, setQ] = useState(92)
  const [scale, setScale] = useState(1)
  const close = () => openDialog(null)
  return (
    <Modal title="Export As" onClose={close} footer={<><button className="btn" onClick={() => { void copyToClipboard(d); close() }}>Copy to Clipboard</button><div className="grow" />
      <button className="btn" onClick={close}>Cancel</button>
      <button className="btn primary" onClick={() => { void exportFile(d, fmt, q / 100, scale); close() }}>Export</button></>}>
      <div className="field"><label>Format</label><Seg value={fmt} onChange={setFmt} options={[['png', 'PNG'], ['jpg', 'JPG'], ['webp', 'WebP']]} /></div>
      {fmt !== 'png' && <Slider label="Quality" value={q} min={1} max={100} onChange={setQ} unit="%" />}
      <div className="field"><label>Scale</label>
        <Seg value={String(scale)} onChange={(v) => setScale(Number(v))} options={[['0.25', '25%'], ['0.5', '50%'], ['1', '100%'], ['2', '200%'], ['3', '300%']]} /></div>
      <div className="muted">Output size: {Math.round(d.w * scale)} × {Math.round(d.h * scale)} px{fmt === 'jpg' ? ' (transparent areas are filled with white)' : ''}</div>
    </Modal>
  )
}

// ------------------------------------------------------------------ canvas / image size
function CanvasSize() {
  const d = curDoc()!
  const [w, setW] = useState(d.w), [h, setH] = useState(d.h), [ax, setAx] = useState(1), [ay, setAy] = useState(1)
  const close = () => openDialog(null)
  return (
    <Modal title="Canvas Size" onClose={close} footer={<><button className="btn" onClick={close}>Cancel</button>
      <button className="btn primary" onClick={() => { d.resizeCanvas(w, h, ax, ay); close() }}>OK</button></>}>
      <div className="muted" style={{ marginBottom: 6 }}>Current: {d.w} × {d.h}</div>
      <div className="row"><NumIn label="Width" value={w} onChange={setW} max={8192} /><NumIn label="Height" value={h} onChange={setH} max={8192} /></div>
      <div className="field" style={{ marginTop: 10 }}><label>Anchor (position of the existing content)</label>
        <div className="anchor">{[0, 1, 2].map((y) => [0, 1, 2].map((x) => <button key={`${x}${y}`} className={ax === x && ay === y ? 'on' : ''} onClick={() => { setAx(x); setAy(y) }} />))}</div></div>
    </Modal>
  )
}
function ImageSize() {
  const d = curDoc()!
  const [w, setW] = useState(d.w), [h, setH] = useState(d.h), [lock, setLock] = useState(true)
  const close = () => openDialog(null)
  return (
    <Modal title="Image Size (scales all layers)" onClose={close} footer={<><button className="btn" onClick={close}>Cancel</button>
      <button className="btn primary" onClick={() => { d.resizeImage(w, h); close() }}>OK</button></>}>
      <div className="muted" style={{ marginBottom: 6 }}>Current: {d.w} × {d.h}</div>
      <div className="row"><NumIn label="Width" value={w} onChange={(v) => { setW(v); if (lock) setH(Math.max(1, Math.round((v * d.h) / d.w))) }} max={8192} />
        <NumIn label="Height" value={h} onChange={(v) => { setH(v); if (lock) setW(Math.max(1, Math.round((v * d.w) / d.h))) }} max={8192} /></div>
      <Check label="Lock aspect ratio" checked={lock} onChange={setLock} />
    </Modal>
  )
}

// ------------------------------------------------------------------ filter (live preview)
function Filter({ id }: { id: string }) {
  const f = findFilter(id)
  const d = curDoc()!
  const L = d.active!
  const [p, setP] = useState(() => defaultParams(f.params))
  const [preview, setPreview] = useState(true)
  const edit = useRef<PixelEdit | null>(null)
  const done = useRef(false)
  const close = () => openDialog(null)
  const name = f.label.replace(/…$/, '')

  useEffect(() => {
    if (L.locked || !L.visible) { hooksToast(L.locked ? 'Layer is locked' : 'Layer is hidden'); done.current = true; close(); return }
    const cv = d.paintTarget(L, 'canvas') // rasterises text/shape/image layers (its own undo step)
    if (!cv) { done.current = true; close(); return }
    d.history.begin(name)
    edit.current = new PixelEdit(d, L, 'canvas')
    return () => { if (!done.current) { edit.current?.restore(); d.touch(L, true); d.history.end() } }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const apply = (params: Record<string, number>, on: boolean) => {
    const e = edit.current
    if (!e) return
    e.restore()
    if (on) {
      const cv = L.canvas, sel = d.sel
      const pad = Math.ceil(f.pad?.(params) ?? 0) + 2
      if (!sel) f.run(cv, { x: 0, y: 0, w: cv.width, h: cv.height }, params)
      else {
        const tmp = mkCanvas(cv.width, cv.height)
        ctx2(tmp).drawImage(cv, 0, 0)
        const r = clipRect({ x: sel.bbox.x - pad, y: sel.bbox.y - pad, w: sel.bbox.w + pad * 2, h: sel.bbox.h + pad * 2 }, cv.width, cv.height)
        f.run(tmp, r, params)
        const tc = ctx2(tmp)
        tc.globalCompositeOperation = 'destination-in'; tc.drawImage(sel.canvas, 0, 0)
        const c = ctx2(cv)
        c.globalCompositeOperation = 'destination-out'; c.drawImage(sel.canvas, 0, 0)
        c.globalCompositeOperation = 'source-over'; c.drawImage(tmp, 0, 0)
      }
    }
    d.touch(L, true)
  }
  const t = useRef<number | undefined>(undefined)
  useEffect(() => {
    clearTimeout(t.current)
    t.current = window.setTimeout(() => apply(p, preview), 50)
    return () => clearTimeout(t.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p, preview])

  const ok = () => {
    clearTimeout(t.current)
    apply(p, true)
    edit.current?.commit(name, d.sel ? d.sel.bbox : null)
    done.current = true
    d.history.end()
    d.changed()
    close()
  }
  const cancel = () => { done.current = true; edit.current?.restore(); d.touch(L, true); d.history.end(); close() }
  return (
    <Modal title={name} width={360} onClose={cancel} footer={<><Check label="Preview" checked={preview} onChange={setPreview} /><div className="grow" />
      <button className="btn" onClick={() => setP(defaultParams(f.params))}>Reset</button><button className="btn" onClick={cancel}>Cancel</button><button className="btn primary" onClick={ok}>OK</button></>}>
      {d.sel && <div className="faint" style={{ marginBottom: 6 }}>Applies to the selection only</div>}
      {f.params.length === 0 && <div className="faint">This filter has no parameters</div>}
      {f.params.map((pd) => <Slider key={pd.key} label={pd.label} value={p[pd.key]} min={pd.min} max={pd.max} step={pd.step ?? 1} onChange={(v) => setP((o) => ({ ...o, [pd.key]: v }))} />)}
    </Modal>
  )
}
function hooksToast(m: string) { void import('./doc').then(({ hooks }) => hooks.toast(m, 'error')) }

// ------------------------------------------------------------------ misc
function Prompt({ s }: { s: Extract<NonNullable<ReturnType<typeof useUI.getState>['dialog']>, { kind: 'prompt' }> }) {
  const [v, setV] = useState(s.value)
  const close = () => openDialog(null)
  const ok = () => { s.onOk(v); close() }
  return (
    <Modal title={s.title} width={320} onClose={close} footer={<><button className="btn" onClick={close}>Cancel</button><button className="btn primary" onClick={ok}>OK</button></>}>
      <Slider label={s.label} value={v} min={s.min} max={s.max} onChange={setV} />
    </Modal>
  )
}
function FillDlg() {
  const d = curDoc()!
  const [src, setSrc] = useState<'fg' | 'bg' | 'white' | 'black' | 'gray'>('fg')
  const [op, setOp] = useState(100)
  const close = () => openDialog(null)
  const s = useUI.getState()
  const color = { fg: s.fg, bg: s.bg, white: '#ffffff', black: '#000000', gray: '#808080' }[src]
  return (
    <Modal title="Fill" width={320} onClose={close} footer={<><button className="btn" onClick={close}>Cancel</button>
      <button className="btn primary" onClick={() => { fillSel(d, color, 'Fill', op / 100); close() }}>OK</button></>}>
      <Seg value={src} onChange={setSrc} options={[['fg', 'Foreground'], ['bg', 'Background'], ['white', 'White'], ['black', 'Black'], ['gray', '50% Gray']]} />
      <div style={{ marginTop: 8 }}><Slider label="Opacity" value={op} min={1} max={100} onChange={setOp} unit="%" /></div>
    </Modal>
  )
}
function CloseConfirm({ id }: { id: string }) {
  const d = useUI.getState().docs.find((x) => x.id === id)
  const close = () => openDialog(null)
  if (!d) return null
  return (
    <Modal title="Close Document" width={340} onClose={close} footer={<><button className="btn" onClick={close}>Cancel</button>
      <button className="btn danger" onClick={() => { closeDoc(id); close() }}>Don't Save</button>
      <button className="btn primary" onClick={async () => { await saveProject(d); closeDoc(id); close() }}>Save Project & Close</button></>}>
      “{d.name}” has unsaved changes.
    </Modal>
  )
}
const KEYS: [string, string][] = [
  ['V / M / L / W / C', 'Move / Marquee / Lasso / Magic Wand (W again: Quick Selection) / Crop'], ['B / E / K / G / I', 'Brush / Eraser / Paint Bucket / Gradient / Eyedropper'],
  ['S / J / R / O', 'Clone Stamp / Spot Healing / Blur-Sharpen-Smudge / Dodge-Burn'], ['T / U / H / Z / A', 'Text / Shape / Hand / Zoom / AI Mark Brush'],
  ['[  ]', 'Decrease / increase brush size'], ['X / D', 'Swap / reset foreground and background colors'], ['Space + drag / middle mouse', 'Pan'], ['Mouse wheel', 'Zoom around the cursor'],
  ['Ctrl+0 / Ctrl+1', 'Fit on screen / 100%'], ['Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y', 'Undo / redo'], ['Ctrl+A / D / Shift+I', 'Select all / deselect / inverse'],
  ['Ctrl+C / X / V', 'Copy / cut / paste (OS clipboard images can be pasted too)'], ['Ctrl+J / Ctrl+Shift+J', 'Layer via copy / via cut'], ['Alt+Backspace / Ctrl+Backspace', 'Fill with foreground / background'],
  ['Delete', 'Clear selection contents (deletes the layer when nothing is selected)'], ['Ctrl+T', 'Free transform (move tool handles)'], ['Ctrl+E', 'Merge down'], ['Ctrl+S', 'Save project'],
  ['Ctrl+G / Ctrl+Shift+G', 'Group / ungroup layers (Ctrl or Shift-click layers to multi-select)'], ['Ctrl+R / Ctrl+;', 'Toggle rulers / guides (drag guides out of the rulers)'],
  ['Arrow keys (Shift x10)', 'Nudge the layer'], ['Shift / Alt + selection drag', 'Add / subtract / (both) intersect; Shift = square, Alt = from center'],
  ['Double-click text / Text tool click', 'Edit text on the canvas (Esc or click away to finish)'],
]
function Help() {
  const close = () => openDialog(null)
  return (
    <Modal title="Keyboard Shortcuts" width={560} onClose={close}>
      <table className="ie-keys"><tbody>{KEYS.map(([k, v]) => <tr key={k}><td><kbd>{k}</kbd></td><td>{v}</td></tr>)}</tbody></table>
    </Modal>
  )
}

export default function Dialogs() {
  const dlg = useUI((s) => s.dialog)
  const hasDoc = !!curDoc()
  if (!dlg) return null
  switch (dlg.kind) {
    case 'new': return <NewDoc />
    case 'export': return hasDoc ? <Export /> : null
    case 'canvasSize': return hasDoc ? <CanvasSize /> : null
    case 'imageSize': return hasDoc ? <ImageSize /> : null
    case 'filter': return hasDoc && curDoc()?.active ? <Filter key={dlg.id} id={dlg.id} /> : null
    case 'prompt': return <Prompt s={dlg} />
    case 'fill': return hasDoc ? <FillDlg /> : null
    case 'help': return <Help />
    case 'closeConfirm': return <CloseConfirm id={dlg.docId} />
  }
}
export type { Doc }
