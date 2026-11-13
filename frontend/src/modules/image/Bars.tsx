import {
  Bandage, Brush, Crop, Droplet, Eclipse, Eraser, Hand, Lasso, Move, PaintBucket, Pipette, SquareDashed, Stamp, Type, WandSparkles, ZoomIn, Shapes, Sun, Sparkles, Crosshair,
} from 'lucide-react'
import type { ComponentType } from 'react'
import { Check, ColorChip, Seg, Slider } from './controls'
import { curDoc, useUI } from './store'
import { applyCrop, cancelCrop, resetCrop } from './tools'
import type { ToolId } from './types'
import { flipLayer } from './ops'

export interface ToolDef { id: ToolId; key: string; label: string; icon: ComponentType<{ size?: number }> }
export const TOOL_DEFS: ToolDef[] = [
  { id: 'move', key: 'V', label: 'Move / Transform', icon: Move },
  { id: 'marquee', key: 'M', label: 'Marquee', icon: SquareDashed },
  { id: 'lasso', key: 'L', label: 'Lasso', icon: Lasso },
  { id: 'wand', key: 'W', label: 'Magic Wand', icon: WandSparkles },
  { id: 'quicksel', key: 'W', label: 'Quick Selection', icon: Crosshair },
  { id: 'crop', key: 'C', label: 'Crop', icon: Crop },
  { id: 'brush', key: 'B', label: 'Brush', icon: Brush },
  { id: 'eraser', key: 'E', label: 'Eraser', icon: Eraser },
  { id: 'bucket', key: 'K', label: 'Paint Bucket', icon: PaintBucket },
  { id: 'gradient', key: 'G', label: 'Gradient', icon: Eclipse },
  { id: 'eyedropper', key: 'I', label: 'Eyedropper', icon: Pipette },
  { id: 'clone', key: 'S', label: 'Clone Stamp (Alt-click to sample)', icon: Stamp },
  { id: 'heal', key: 'J', label: 'Spot Healing', icon: Bandage },
  { id: 'retouch', key: 'R', label: 'Blur / Sharpen / Smudge', icon: Droplet },
  { id: 'tone', key: 'O', label: 'Dodge / Burn', icon: Sun },
  { id: 'text', key: 'T', label: 'Text', icon: Type },
  { id: 'shape', key: 'U', label: 'Shape', icon: Shapes },
  { id: 'aibrush', key: 'A', label: 'AI Mark Brush (paint the area to erase)', icon: Sparkles },
  { id: 'hand', key: 'H', label: 'Hand (Space)', icon: Hand },
  { id: 'zoom', key: 'Z', label: 'Zoom', icon: ZoomIn },
]

export function Toolbar() {
  const tool = useUI((s) => s.tool), fg = useUI((s) => s.fg), bg = useUI((s) => s.bg)
  const set = useUI((s) => s.setTool)
  return (
    <div className="ie-toolbar">
      <div className="tools">
        {TOOL_DEFS.map((t) => (
          <button key={t.id} className={tool === t.id ? 'on' : ''} title={`${t.label} (${t.key})`} onClick={() => set(t.id)} data-tool={t.id}>
            <t.icon size={17} />
          </button>
        ))}
      </div>
      <div className="grow" />
      <div className="fgbg" title="Foreground / background color (X swap, D default)">
        <div className="bgc" style={{ background: bg }}>
          <ColorChip value={bg} onChange={(c) => useUI.getState().setBg(c)} style={{ position: 'absolute', inset: 0 }} />
        </div>
        <div className="fgc" style={{ background: fg }}>
          <ColorChip value={fg} onChange={(c) => useUI.getState().setFg(c)} style={{ position: 'absolute', inset: 0 }} />
        </div>
        <button className="swap" title="Swap (X)" onClick={() => { const s = useUI.getState(); const f = s.fg; s.setFg(s.bg); s.setBg(f) }}>⇄</button>
        <button className="reset" title="Default colors (D)" onClick={() => { useUI.getState().setFg('#000000'); useUI.getState().setBg('#ffffff') }}>◩</button>
      </div>
    </div>
  )
}

const RATIOS: [string, string][] = [['free', 'Free'], ['orig', 'Original ratio'], ['1:1', '1:1'], ['4:3', '4:3'], ['3:2', '3:2'], ['16:9', '16:9'], ['9:16', '9:16'], ['4:5', '4:5'], ['2:3', '2:3'], ['custom', 'Custom']]

export function OptionsBar() {
  const tool = useUI((s) => s.tool), o = useUI((s) => s.opts), setO = useUI((s) => s.setOpts)
  useUI((s) => s.rev)
  const d = curDoc()
  const selMode = (
    <Seg value={o.selMode} onChange={(v) => setO({ selMode: v })} options={[['replace', 'New', 'New selection'], ['add', 'Add', 'Add to selection (Shift)'], ['subtract', 'Sub', 'Subtract from selection (Alt)'], ['intersect', 'Int', 'Intersect with selection (Shift+Alt)']]} />
  )
  const brushCommon = (
    <>
      <Slider label="Size" value={o.brushSize} min={1} max={500} onChange={(v) => setO({ brushSize: v })} unit="px" />
      <Slider label="Hardness" value={o.hardness} min={0} max={100} onChange={(v) => setO({ hardness: v })} unit="%" />
    </>
  )
  const opacity = <Slider label="Opacity" value={o.opacity} min={1} max={100} onChange={(v) => setO({ opacity: v })} unit="%" />
  const tolerance = <Slider label="Tolerance" value={o.tolerance} min={0} max={255} onChange={(v) => setO({ tolerance: v })} />
  const snapBox = <Check label="Snap" checked={useUI.getState().snap} onChange={(v) => useUI.setState({ snap: v })} title="Snap to guides and canvas edges" />
  return (
    <div className="ie-options">
      <span className="tname">{TOOL_DEFS.find((t) => t.id === tool)?.label.replace(/ \(.*\)/, '')}</span>
      {tool === 'move' && (
        <>
          <Check label="Auto-select layer" checked={o.autoSelect} onChange={(v) => setO({ autoSelect: v })} title="Click the canvas to pick the layer under the cursor (with a selection, moves the selected pixels)" />
          {snapBox}
          <button className="btn sm" disabled={!d?.active || d.active.type === 'group'} onClick={() => d && flipLayer(d, true)}>Flip H</button>
          <button className="btn sm" disabled={!d?.active || d.active.type === 'group'} onClick={() => d && flipLayer(d, false)}>Flip V</button>
          <button className="btn sm" disabled={!d?.active} onClick={() => d && alignLayer(d, 'h')}>Center H</button>
          <button className="btn sm" disabled={!d?.active} onClick={() => d && alignLayer(d, 'v')}>Center V</button>
          <span className="faint">Drag inside the box to move · handles scale (Shift = keep ratio, Alt = from center) · outside a corner rotates (Shift = 15°) · arrow keys nudge · Ctrl-drag moves the whole layer · drag guides from the rulers</span>
        </>
      )}
      {tool === 'marquee' && (
        <>
          <Seg value={o.marquee} onChange={(v) => setO({ marquee: v })} options={[['rect', 'Rectangle'], ['ellipse', 'Ellipse']]} />
          {selMode}
          <Slider label="Feather" value={o.feather} min={0} max={100} onChange={(v) => setO({ feather: v })} unit="px" />
          <Seg value={o.marqueeStyle} onChange={(v) => setO({ marqueeStyle: v })} options={[['normal', 'Normal'], ['ratio', 'Fixed ratio'], ['size', 'Fixed size']]} />
          {o.marqueeStyle !== 'normal' && (
            <>
              <input className="input num2" type="number" value={o.mqW} min={1} onChange={(e) => setO({ mqW: Math.max(1, Number(e.target.value)) })} />×
              <input className="input num2" type="number" value={o.mqH} min={1} onChange={(e) => setO({ mqH: Math.max(1, Number(e.target.value)) })} />
            </>
          )}
          {snapBox}
        </>
      )}
      {tool === 'lasso' && (
        <>
          <Seg value={o.lasso} onChange={(v) => setO({ lasso: v })} options={[['free', 'Freehand'], ['poly', 'Polygonal']]} />
          {selMode}
          <Slider label="Feather" value={o.feather} min={0} max={100} onChange={(v) => setO({ feather: v })} unit="px" />
          <span className="faint">Polygonal: click to add points, double-click / Enter to close, Backspace removes a point, Esc cancels</span>
        </>
      )}
      {tool === 'wand' && (
        <>
          {selMode}{tolerance}
          <Check label="Contiguous" checked={o.contiguous} onChange={(v) => setO({ contiguous: v })} />
          <Check label="Sample all layers" checked={o.sampleAll} onChange={(v) => setO({ sampleAll: v })} />
          <Slider label="Feather" value={o.feather} min={0} max={50} onChange={(v) => setO({ feather: v })} unit="px" />
          <span className="faint">Press W again for Quick Selection</span>
        </>
      )}
      {tool === 'quicksel' && (
        <>
          {selMode}
          <Slider label="Size" value={o.brushSize} min={4} max={300} onChange={(v) => setO({ brushSize: v })} unit="px" />
          {tolerance}
          <Check label="Sample all layers" checked={o.sampleAll} onChange={(v) => setO({ sampleAll: v })} />
          <span className="faint">Paint over the object to select it; Alt = subtract, Shift = add. Select ▸ Color Range… selects by color.</span>
        </>
      )}
      {tool === 'crop' && (
        <>
          <select className="select sm" value={o.cropRatio} onChange={(e) => setO({ cropRatio: e.target.value })}>
            {RATIOS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          {o.cropRatio === 'custom' && (
            <>
              <input className="input num2" type="number" value={o.cropW} min={1} onChange={(e) => setO({ cropW: Math.max(1, Number(e.target.value)) })} />:
              <input className="input num2" type="number" value={o.cropH} min={1} onChange={(e) => setO({ cropH: Math.max(1, Number(e.target.value)) })} />
            </>
          )}
          <button className="btn sm primary" onClick={() => applyCrop()}>Apply Crop (Enter)</button>
          <button className="btn sm" onClick={() => d && resetCrop(d)}>Reset</button>
          <button className="btn sm" onClick={() => cancelCrop()}>Cancel (Esc)</button>
          {snapBox}
          <span className="faint">Drag out a crop box; a box extending past the canvas expands it</span>
        </>
      )}
      {(tool === 'brush' || tool === 'eraser') && (
        <>{brushCommon}{opacity}
          <Slider label="Flow" value={o.flow} min={1} max={100} onChange={(v) => setO({ flow: v })} unit="%" />
          <Slider label="Smoothing" value={o.smooth} min={0} max={100} onChange={(v) => setO({ smooth: v })} unit="%" />
          <span className="faint">[ ] change size · Shift-click draws a straight line</span>
        </>
      )}
      {tool === 'clone' && (
        <>{brushCommon}{opacity}
          <Slider label="Flow" value={o.flow} min={1} max={100} onChange={(v) => setO({ flow: v })} unit="%" />
          <Check label="Aligned" checked={o.cloneAligned} onChange={(v) => setO({ cloneAligned: v })} />
          <span className="faint">Alt-click to set the sample source, then paint</span>
        </>
      )}
      {tool === 'heal' && (<><Slider label="Size" value={o.brushSize} min={2} max={300} onChange={(v) => setO({ brushSize: v })} unit="px" /><span className="faint">Paint over a blemish; on release it is filled from the surrounding pixels (a selection limits the area)</span></>)}
      {tool === 'aibrush' && (<><Slider label="Size" value={o.brushSize} min={2} max={300} onChange={(v) => setO({ brushSize: v })} unit="px" /><span className="faint">Paint over what to erase; on release it is added to the selection. Then press AI Erase / Inpaint in the AI panel</span></>)}
      {tool === 'retouch' && (
        <>
          <Seg value={o.retouch} onChange={(v) => setO({ retouch: v })} options={[['blur', 'Blur'], ['sharpen', 'Sharpen'], ['smudge', 'Smudge']]} />
          {brushCommon}
          <Slider label="Strength" value={o.strength} min={1} max={100} onChange={(v) => setO({ strength: v })} unit="%" />
        </>
      )}
      {tool === 'tone' && (
        <>
          <Seg value={o.tone} onChange={(v) => setO({ tone: v })} options={[['dodge', 'Dodge'], ['burn', 'Burn']]} />
          {brushCommon}
          <Slider label="Exposure" value={o.exposure} min={1} max={100} onChange={(v) => setO({ exposure: v })} unit="%" />
        </>
      )}
      {tool === 'bucket' && (
        <>{tolerance}
          <Check label="Contiguous" checked={o.contiguous} onChange={(v) => setO({ contiguous: v })} />
          <Check label="All layers" checked={o.bucketAll} onChange={(v) => setO({ bucketAll: v })} />
          {opacity}
        </>
      )}
      {tool === 'gradient' && (
        <>
          <Seg value={o.gradType} onChange={(v) => setO({ gradType: v })} options={[['linear', 'Linear'], ['radial', 'Radial']]} />
          <Seg value={o.gradMode} onChange={(v) => setO({ gradMode: v })} options={[['fgbg', 'Foreground to Background'], ['fgtrans', 'Foreground to Transparent']]} />
          {opacity}
          <span className="faint">Drag to draw the gradient; Shift constrains to 45°</span>
        </>
      )}
      {tool === 'eyedropper' && <span className="faint">Click to sample a color (drag for live sampling); Alt-click sets the background color</span>}
      {tool === 'text' && <span className="faint">Click the canvas to type directly on it (Esc or click away to finish); font, size, color, stroke and shadow are in the Properties panel</span>}
      {tool === 'shape' && (
        <>
          <Seg value={o.shape} onChange={(v) => setO({ shape: v })} options={[['rect', 'Rectangle'], ['ellipse', 'Ellipse'], ['line', 'Line'], ['polygon', 'Polygon'], ['star', 'Star']]} />
          <Check label="Fill (foreground)" checked={o.shapeFill} onChange={(v) => setO({ shapeFill: v })} />
          <Check label="Stroke (background)" checked={o.shapeStroke} onChange={(v) => setO({ shapeStroke: v })} />
          <Slider label="Stroke" value={o.shapeStrokeW} min={1} max={60} onChange={(v) => setO({ shapeStrokeW: v })} />
          {o.shape === 'rect' && <Slider label="Radius" value={o.shapeRadius} min={0} max={200} onChange={(v) => setO({ shapeRadius: v })} />}
          {o.shape === 'polygon' && <Slider label="Sides" value={o.shapeSides} min={3} max={16} onChange={(v) => setO({ shapeSides: v })} />}
          {snapBox}
        </>
      )}
      {tool === 'hand' && <span className="faint">Drag to pan; Ctrl+0 fits the canvas on screen</span>}
      {tool === 'zoom' && <span className="faint">Click to zoom in, Alt-click to zoom out; the mouse wheel zooms around the cursor</span>}
    </div>
  )
}

function alignLayer(d: NonNullable<ReturnType<typeof curDoc>>, axis: 'h' | 'v') {
  const L = d.active
  if (!L || L.locked) return
  const b = d.layerBounds(L)
  const dx = axis === 'h' ? d.w / 2 - (b.x + b.w / 2) : 0, dy = axis === 'v' ? d.h / 2 - (b.y + b.h / 2) : 0
  if (L.type === 'group') {
    d.history.group('Align', () => { for (const x of d.descendants(L)) if (x.type !== 'group') d.mutate(x, 'Align', (l) => { l.t.cx += dx; l.t.cy += dy }) })
    return
  }
  d.mutate(L, 'Align', (l) => { l.t.cx += dx; l.t.cy += dy })
}
