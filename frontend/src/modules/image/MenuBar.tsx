import { useEffect, useRef, useState } from 'react'
import { cmd } from './commands'
import { openColorRange } from './ColorRange'
import { ADJ_FILTERS, FILTER_DEFS } from './fx'
import { sendToLibrary } from './io'
import { strokeSel } from './ops'
import { curDoc, openDialog, useUI } from './store'
import { openAiPanel } from './AiPanel'
import { resetCrop } from './tools'
import { imageSession } from './ImageProjects'
import { SaveChip } from '../../shared/projects/ProjectHome'

interface Item { label?: string; hint?: string; run?: () => void; disabled?: boolean; sub?: Item[]; sep?: boolean; checked?: boolean }

export default function MenuBar() {
  useUI((s) => s.rev)
  const rulers = useUI((s) => s.rulers), showGuides = useUI((s) => s.showGuides), snap = useUI((s) => s.snap)
  const d = curDoc()
  const [open, setOpen] = useState<number | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open === null) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(null) }
    window.addEventListener('mousedown', h)
    return () => window.removeEventListener('mousedown', h)
  }, [open])
  const L = d?.active
  const has = !!d, hasSel = !!d?.sel
  const sep: Item = { sep: true }
  const filterDlg = (id: string) => () => openDialog({ kind: 'filter', id })

  const menus: [string, Item[]][] = [
    ['File', [
      { label: 'Projects (New / Recent)…', run: () => { void imageSession.flush(true); imageSession.showHome(true) } },
      sep,
      { label: 'New…', hint: 'Ctrl+N', run: cmd.newDoc },
      { label: 'Open Image / Project…', hint: 'Ctrl+O', run: cmd.openFile },
      { label: 'Place Image…', run: cmd.placeFile, disabled: !has },
      sep,
      { label: 'Save as File (.cutlab-psd.json)', hint: 'Ctrl+S', run: cmd.save, disabled: !has },
      { label: 'Export As…', hint: 'Ctrl+Shift+E', run: cmd.exportAs, disabled: !has },
      { label: 'Quick Export PNG', run: cmd.quickPng, disabled: !has },
      { label: 'Copy Merged to Clipboard', run: cmd.copyMerged, disabled: !has },
      sep,
      { label: 'Send to Library', run: () => d && void sendToLibrary(d, false), disabled: !has },
      { label: 'Send to Video Editor', run: () => d && void sendToLibrary(d, true), disabled: !has },
      sep,
      { label: 'Close Document', hint: 'Ctrl+W', run: cmd.closeDoc, disabled: !has },
    ]],
    ['Edit', [
      { label: 'Undo', hint: 'Ctrl+Z', run: cmd.undo, disabled: !d?.history.canUndo },
      { label: 'Redo', hint: 'Ctrl+Shift+Z / Ctrl+Y', run: cmd.redo, disabled: !d?.history.canRedo },
      sep,
      { label: 'Cut', hint: 'Ctrl+X', run: cmd.cut, disabled: !has },
      { label: 'Copy', hint: 'Ctrl+C', run: cmd.copy, disabled: !has },
      { label: 'Paste', hint: 'Ctrl+V', run: cmd.paste, disabled: !has },
      { label: 'Clear', hint: 'Delete', run: cmd.clear, disabled: !has },
      sep,
      { label: 'Fill with Foreground', hint: 'Alt+Backspace', run: cmd.fillFg, disabled: !has },
      { label: 'Fill with Background', hint: 'Ctrl+Backspace', run: cmd.fillBg, disabled: !has },
      { label: 'Fill…', run: () => openDialog({ kind: 'fill' }), disabled: !has },
      { label: 'Stroke Selection', run: () => d && strokeSel(d, useUI.getState().fg, 4), disabled: !hasSel },
      sep,
      { label: 'Free Transform', hint: 'Ctrl+T', run: () => useUI.getState().setTool('move'), disabled: !has },
    ]],
    ['Image', [
      { label: 'Image Size…', run: () => openDialog({ kind: 'imageSize' }), disabled: !has },
      { label: 'Canvas Size…', run: () => openDialog({ kind: 'canvasSize' }), disabled: !has },
      { label: 'Rotate Canvas', disabled: !has, sub: [
        { label: '90° Clockwise', run: () => d?.rotateCanvas(90) }, { label: '90° Counter-clockwise', run: () => d?.rotateCanvas(-90) }, { label: '180°', run: () => d?.rotateCanvas(180) }] },
      { label: 'Flip Canvas', disabled: !has, sub: [{ label: 'Flip Horizontal', run: () => d?.flipCanvas(true) }, { label: 'Flip Vertical', run: () => d?.flipCanvas(false) }] },
      { label: 'Crop to Selection', disabled: !hasSel, run: () => { if (d?.sel) { const b = d.sel.bbox; d.cropTo(b.x, b.y, b.w, b.h) } } },
      { label: 'Crop Tool', run: () => { useUI.getState().setTool('crop'); if (d) resetCrop(d) }, disabled: !has },
      sep,
      { label: 'Adjustments (apply directly)', disabled: !has, sub: ADJ_FILTERS.map((f) => ({ label: f.label, run: filterDlg(f.id) })) },
    ]],
    ['Layer', [
      { label: 'New Layer', hint: 'Ctrl+Shift+N', run: cmd.newLayer, disabled: !has },
      { label: 'Duplicate Layer', run: cmd.duplicateLayer, disabled: !L },
      { label: 'Delete Layer', run: cmd.deleteLayer, disabled: !L },
      sep,
      { label: 'Group Layers', hint: 'Ctrl+G', run: cmd.group, disabled: !L },
      { label: 'Ungroup Layers', hint: 'Ctrl+Shift+G', run: cmd.ungroup, disabled: L?.type !== 'group' },
      { label: 'Merge Group', run: () => d && L && d.flattenGroup(L), disabled: L?.type !== 'group' },
      sep,
      { label: 'Layer via Copy', hint: 'Ctrl+J', run: cmd.layerViaCopy, disabled: !L },
      { label: 'Layer via Cut', hint: 'Ctrl+Shift+J', run: cmd.layerViaCut, disabled: !L || !hasSel },
      sep,
      { label: L?.mask ? 'Enable / Disable Layer Mask' : 'Add Layer Mask', run: cmd.toggleMask, disabled: !L },
      { label: 'Add Mask from Selection', run: () => { if (d && L && !L.mask) d.addMask(L, true) }, disabled: !L || !!L?.mask || !hasSel },
      { label: 'Invert Mask', run: () => { if (d && L) d.invertMask(L) }, disabled: !L?.mask },
      { label: 'Apply Mask', run: () => { if (d && L) d.removeMask(L, true) }, disabled: !L?.mask },
      { label: 'Delete Mask', run: () => { if (d && L) d.removeMask(L, false) }, disabled: !L?.mask },
      { label: 'Create / Release Clipping Mask', hint: 'Alt+Ctrl+G', run: cmd.clipToBelow, disabled: !L },
      { label: 'Rasterize Layer', run: cmd.rasterize, disabled: !L || L.type === 'raster' || L.type === 'group' },
      sep,
      { label: 'Bring Forward', hint: 'Ctrl+]', run: cmd.moveUp, disabled: !L }, { label: 'Send Backward', hint: 'Ctrl+[', run: cmd.moveDown, disabled: !L },
      { label: 'Bring to Front', run: cmd.toTop, disabled: !L }, { label: 'Send to Back', run: cmd.toBottom, disabled: !L },
      sep,
      { label: 'Merge Down', hint: 'Ctrl+E', run: cmd.mergeDown, disabled: !L },
      { label: 'Merge Visible', run: cmd.mergeVisible, disabled: !has },
      { label: 'Flatten Image', run: cmd.flatten, disabled: !has },
    ]],
    ['Select', [
      { label: 'All', hint: 'Ctrl+A', run: cmd.selectAll, disabled: !has },
      { label: 'Deselect', hint: 'Ctrl+D', run: cmd.deselect, disabled: !hasSel },
      { label: 'Inverse', hint: 'Ctrl+Shift+I', run: cmd.invertSel, disabled: !has },
      sep,
      { label: 'Color Range…', run: openColorRange, disabled: !has },
      { label: 'Load Layer Pixels as Selection', run: () => d && L && d.selFromLayerAlpha(L), disabled: !L },
      sep,
      { label: 'Feather…', run: cmd.featherSel, disabled: !hasSel }, { label: 'Grow…', run: cmd.growSel, disabled: !hasSel },
      { label: 'Shrink…', run: cmd.shrinkSel, disabled: !hasSel }, { label: 'Border…', run: cmd.borderSel, disabled: !hasSel },
    ]],
    ['AI', [
      { label: 'Remove Background → New Layer', run: () => openAiPanel('removebg-layer'), disabled: !L || L.type === 'group' },
      { label: 'Remove Background → Layer Mask', run: () => openAiPanel('removebg-mask'), disabled: !L || L.type === 'group' },
      sep,
      { label: 'Erase / Inpaint Selection', run: () => openAiPanel('inpaint'), disabled: !hasSel },
      { label: 'AI Mark Brush', hint: 'A', run: () => useUI.getState().setTool('aibrush'), disabled: !has },
      sep,
      { label: 'Upscale 2×', run: () => openAiPanel('upscale2'), disabled: !L || L.type === 'group' },
      { label: 'Upscale 4×', run: () => openAiPanel('upscale4'), disabled: !L || L.type === 'group' },
      sep,
      { label: 'Show AI Panel', run: () => openAiPanel() },
    ]],
    ['Filter', FILTER_DEFS.map((f) => ({ label: f.label, run: filterDlg(f.id), disabled: !L || L.type === 'group' }))],
    ['View', [
      { label: 'Zoom In', hint: 'Ctrl++', run: cmd.zoomIn, disabled: !has }, { label: 'Zoom Out', hint: 'Ctrl+-', run: cmd.zoomOut, disabled: !has },
      { label: 'Fit on Screen', hint: 'Ctrl+0', run: cmd.fit, disabled: !has }, { label: 'Actual Pixels (100%)', hint: 'Ctrl+1', run: cmd.zoom100, disabled: !has },
      sep,
      { label: 'Rulers', hint: 'Ctrl+R', run: cmd.toggleRulers, checked: rulers },
      { label: 'Show Guides', hint: 'Ctrl+;', run: cmd.toggleGuides, checked: showGuides },
      { label: 'Snap to Guides & Edges', hint: 'Ctrl+Shift+;', run: cmd.toggleSnap, checked: snap },
      { label: 'New Horizontal Guide…', run: () => cmd.newGuide('h'), disabled: !has },
      { label: 'New Vertical Guide…', run: () => cmd.newGuide('v'), disabled: !has },
      { label: 'Clear Guides', run: cmd.clearGuides, disabled: !d?.guides.length },
      sep,
      { label: 'Keyboard Shortcuts…', run: () => openDialog({ kind: 'help' }) },
    ]],
  ]

  const renderItems = (items: Item[]) => items.map((it, i) =>
    it.sep ? <div key={i} className="msep" /> : (
      <div key={i} className={`mitem ${it.disabled ? 'dis' : ''} ${it.sub ? 'has-sub' : ''}`}
        onClick={() => { if (it.disabled || it.sub) return; setOpen(null); it.run?.() }}>
        <span><span className="chk">{it.checked ? '✓' : ''}</span>{it.label}</span><span className="hint">{it.sub ? '▸' : it.hint}</span>
        {it.sub && !it.disabled && <div className="msub">{renderItems(it.sub)}</div>}
      </div>
    ))

  return (
    <div className="ie-menubar" ref={ref}>
      {menus.map(([name, items], i) => (
        <div key={name} className={`mtop ${open === i ? 'on' : ''}`} onMouseDown={() => setOpen(open === i ? null : i)} onMouseEnter={() => open !== null && setOpen(i)}>
          {name}
          {open === i && <div className="mdrop" onMouseDown={(e) => e.stopPropagation()}>{renderItems(items)}</div>}
        </div>
      ))}
      <div className="grow" />
      {d && <span className="faint" style={{ paddingRight: 10 }}>{d.name}{d.dirty ? ' •' : ''}</span>}
      <span style={{ paddingRight: 10 }}><SaveChip session={imageSession} /></span>
    </div>
  )
}
