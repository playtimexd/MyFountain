import { ChevronDown, ChevronRight, Copy, Eye, EyeOff, Folder, FolderOpen, FolderOutput, FolderPlus, Layers as LayersIcon, Lock, Merge, Plus, Trash2, Unlock, VenetianMask } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cmd } from './commands'
import { ColorPicker, RecentSwatches, Slider } from './controls'
import { hooks } from './doc'
import type { Doc } from './doc'
import { BLEND_MODES, type BlendMode, type Layer } from './types'
import { curDoc, useUI } from './store'
import { ctx2 } from './util'

// ---------------------------------------------------------------- colour
export function ColorPanel() {
  const fg = useUI((s) => s.fg), bg = useUI((s) => s.bg)
  const [which, setWhich] = useState<'fg' | 'bg'>('fg')
  const cur = which === 'fg' ? fg : bg
  const set = (c: string) => (which === 'fg' ? useUI.getState().setFg(c) : useUI.getState().setBg(c))
  return (
    <div className="ie-colorpanel">
      <div className="row" style={{ gap: 8, marginBottom: 6 }}>
        <button className={`ie-which ${which === 'fg' ? 'on' : ''}`} onClick={() => setWhich('fg')}><i style={{ background: fg }} />Foreground</button>
        <button className={`ie-which ${which === 'bg' ? 'on' : ''}`} onClick={() => setWhich('bg')}><i style={{ background: bg }} />Background</button>
        <button className="btn ghost sm" onClick={() => { const s = useUI.getState(); const f = s.fg; s.setFg(s.bg); s.setBg(f) }} title="Swap (X)">⇄</button>
        <button className="btn ghost sm" onClick={() => { useUI.getState().setFg('#000000'); useUI.getState().setBg('#ffffff') }} title="Default colors (D)">◩</button>
      </div>
      <ColorPicker value={cur} onChange={set} />
      <RecentSwatches onPick={(c) => { set(c) }} />
    </div>
  )
}

// ---------------------------------------------------------------- history
export function HistoryPanel() {
  useUI((s) => s.rev)
  const d = curDoc()
  if (!d) return <div className="faint pad">No document</div>
  const h = d.history
  return (
    <div className="ie-history">
      <div className={`hrow ${h.pos === 0 ? 'cur' : ''}`} onClick={() => { h.jump(0); d.afterHistory(true) }}>Open</div>
      {h.entries.map((e, i) => (
        <div key={i} className={`hrow ${h.pos === i + 1 ? 'cur' : ''} ${i + 1 > h.pos ? 'redo' : ''}`} onClick={() => { h.jump(i + 1); d.afterHistory(true) }}>{e.name}</div>
      ))}
      <div className="faint" style={{ padding: '6px 8px', fontSize: 11 }}>{h.entries.length} steps · approx. {(h.bytes / 1e6).toFixed(1)} MB / {(h.budget / 1e6).toFixed(0)} MB</div>
    </div>
  )
}

// ---------------------------------------------------------------- layers
function Thumb({ L, doc, mask }: { L: Layer; doc: Doc; mask?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const cv = ref.current
    if (!cv) return
    const c = ctx2(cv)
    const W = 38, H = 38
    c.clearRect(0, 0, W, H)
    const src = mask ? L.mask?.canvas : L.canvas
    if (!src || !src.width || !src.height) return
    const sw = mask ? doc.w : src.width, sh = mask ? doc.h : src.height
    const k = Math.min(W / sw, H / sh), w = sw * k, h = sh * k, x = (W - w) / 2, y = (H - h) / 2
    c.fillStyle = '#fff'; c.fillRect(x, y, w, h)
    c.fillStyle = '#d4d6dc'
    for (let yy = 0; yy < h; yy += 6) for (let xx = (yy / 6) % 2 ? 0 : 6; xx < w; xx += 12) c.fillRect(x + xx, y + yy, Math.min(6, w - xx), Math.min(6, h - yy))
    if (mask) { c.fillStyle = '#000'; c.fillRect(x, y, w, h) }
    c.imageSmoothingQuality = 'high'
    if (mask) { const t = document.createElement('canvas'); t.width = src.width; t.height = src.height; const tc = ctx2(t); tc.fillStyle = '#fff'; tc.fillRect(0, 0, t.width, t.height); tc.globalCompositeOperation = 'destination-in'; tc.drawImage(src, 0, 0); c.drawImage(t, x, y, w, h) }
    else c.drawImage(src, x, y, w, h)
  }, [L.rev, L.px, L.canvas, mask, doc.w, doc.h, L.mask?.canvas])
  return <canvas ref={ref} width={38} height={38} className="thumb" />
}

export function LayersPanel() {
  useUI((s) => s.rev)
  const d = curDoc()
  const [dragId, setDragId] = useState<string | null>(null)
  const [over, setOver] = useState<{ id: string; zone: 'above' | 'below' | 'into' } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  if (!d) return <div className="faint pad">No document</div>
  const A = d.active
  const rows = d.rows()
  const isGroup = A?.type === 'group'
  const blendValue = isGroup && A!.pass !== false ? 'pass' : A?.blend ?? 'normal'
  const select = (L: Layer, e: React.MouseEvent) => {
    if (e.ctrlKey || e.metaKey) {
      if (!d.multi.size && d.activeId) d.multi.add(d.activeId)
      if (d.multi.has(L.id)) d.multi.delete(L.id); else d.multi.add(L.id)
      d.activeId = L.id
    } else if (e.shiftKey && d.activeId) {
      const a = rows.findIndex((r) => r.L.id === d.activeId), b = rows.findIndex((r) => r.L.id === L.id)
      d.multi = new Set(rows.slice(Math.min(a, b), Math.max(a, b) + 1).map((r) => r.L.id))
    } else { d.multi.clear(); d.activeId = L.id }
    L.editMask = L.editMask && !!L.mask
    hooks.ui(); hooks.render()
  }
  const drop = (T: Layer, zone: 'above' | 'below' | 'into') => {
    if (!dragId || dragId === T.id) return
    const F = d.layer(dragId)
    if (!F) return
    if (zone === 'into' && T.type === 'group') { d.moveNode(F.id, T.id, d.childrenOf(T.id).length); return }
    const sibs = d.childrenOf(T.parent).filter((x) => x !== F)
    const ti = sibs.indexOf(T)
    d.moveNode(F.id, T.parent, zone === 'above' ? ti + 1 : ti)
  }
  return (
    <div className="ie-layers">
      <div className="lctl">
        <select className="select sm" value={blendValue} disabled={!A} onChange={(e) => {
          if (!A) return
          const v = e.target.value
          d.mutate(A, 'Blend mode', (l) => { if (v === 'pass') l.pass = true; else { l.blend = v as BlendMode; if (l.type === 'group') l.pass = false } })
        }}>
          {isGroup && <option value="pass">Pass Through</option>}
          {BLEND_MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <Slider label="Opacity" value={Math.round((A?.opacity ?? 1) * 100)} min={0} max={100} unit="%" onChange={(v) => A && d.mutate(A, 'Layer opacity', (l) => { l.opacity = v / 100 }, 'opacity')} />
      </div>
      <div className="llist">
        {rows.map(({ L, depth }) => (
          <div key={L.id}
            className={`lrow ${L.id === d.activeId ? 'on' : ''} ${d.multi.has(L.id) ? 'multi' : ''} ${over?.id === L.id ? 'ov-' + over.zone : ''} ${L.clip ? 'clip' : ''} ${L.type === 'group' ? 'group' : ''}`}
            style={{ paddingLeft: 6 + depth * 14 + (L.clip ? 14 : 0) }}
            draggable onDragStart={(e) => { setDragId(L.id); e.dataTransfer.setData('text/plain', L.id) }}
            onDragOver={(e) => {
              e.preventDefault()
              const r = e.currentTarget.getBoundingClientRect(), rel = (e.clientY - r.top) / r.height
              setOver({ id: L.id, zone: L.type === 'group' && rel > 0.28 && rel < 0.72 ? 'into' : rel < 0.5 ? 'above' : 'below' })
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => { e.preventDefault(); e.stopPropagation(); if (over) drop(L, over.zone); setDragId(null); setOver(null) }}
            onDragEnd={() => { setDragId(null); setOver(null) }}
            onClick={(e) => select(L, e)}>
            {L.type === 'group'
              ? <button className="icon" title={L.collapsed ? 'Expand' : 'Collapse'} onClick={(e) => { e.stopPropagation(); L.collapsed = !L.collapsed; d.revision++; hooks.ui() }}>{L.collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}</button>
              : <span style={{ width: 18 }} />}
            <button className="icon" title="Show / hide" onClick={(e) => { e.stopPropagation(); d.mutate(L, L.visible ? 'Hide layer' : 'Show layer', (l) => { l.visible = !l.visible }) }}>
              {L.visible ? <Eye size={14} /> : <EyeOff size={14} className="faint" />}
            </button>
            {L.type === 'group'
              ? <div className="thumbwrap grp"><div className="gicon">{L.collapsed ? <Folder size={20} /> : <FolderOpen size={20} />}</div></div>
              : <div className={`thumbwrap ${!L.editMask ? 'sel' : ''}`} onClick={(e) => { e.stopPropagation(); d.activeId = L.id; L.editMask = false; hooks.ui() }}><Thumb L={L} doc={d} /></div>}
            {L.mask && (
              <div className={`thumbwrap mask ${L.editMask ? 'sel' : ''} ${L.mask.enabled ? '' : 'off'}`} title="Layer mask (click to edit; white = show, black = hide)"
                onClick={(e) => { e.stopPropagation(); d.activeId = L.id; L.editMask = true; hooks.ui(); hooks.render() }}><Thumb L={L} doc={d} mask /></div>
            )}
            <div className="lname" onDoubleClick={() => setEditing(L.id)}>
              {editing === L.id ? (
                <input autoFocus className="input" defaultValue={L.name} onClick={(e) => e.stopPropagation()}
                  onBlur={(e) => { setEditing(null); if (e.target.value && e.target.value !== L.name) d.mutate(L, 'Rename layer', (l) => { l.name = e.target.value }) }}
                  onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(null) }} />
              ) : <><span className="n">{L.name}</span><span className="faint t">{{ raster: '', image: 'Image', text: 'Text', shape: 'Shape', group: `Group · ${d.childrenOf(L.id).length}` }[L.type]}{L.clip ? ' ↳ clipped' : ''}{L.adjust.length ? ' · adjusted' : ''}</span></>}
            </div>
            <button className="icon" title="Lock" onClick={(e) => { e.stopPropagation(); d.mutate(L, L.locked ? 'Unlock layer' : 'Lock layer', (l) => { l.locked = !l.locked }) }}>
              {L.locked ? <Lock size={13} /> : <Unlock size={13} className="faint" />}
            </button>
          </div>
        ))}
      </div>
      <div className="lbtns">
        <button className="btn ghost icon sm" title="New layer (Ctrl+Shift+N)" onClick={cmd.newLayer}><Plus size={15} /></button>
        <button className="btn ghost icon sm" title="Group layers (Ctrl+G) — Ctrl/Shift-click to select several" disabled={!A} onClick={cmd.group}><FolderPlus size={15} /></button>
        <button className="btn ghost icon sm" title="Ungroup (Ctrl+Shift+G)" disabled={!isGroup} onClick={cmd.ungroup}><FolderOutput size={15} /></button>
        <button className="btn ghost icon sm" title="Duplicate layer" disabled={!A} onClick={cmd.duplicateLayer}><Copy size={15} /></button>
        <button className="btn ghost icon sm" title={A?.mask ? 'Delete mask' : 'Add mask (uses the selection if there is one)'} disabled={!A}
          onClick={() => { if (!A) return; if (A.mask) d.removeMask(A, false); else d.addMask(A, !!d.sel) }}><VenetianMask size={15} /></button>
        <button className="btn ghost icon sm" title={isGroup ? 'Merge group' : 'Merge down (Ctrl+E)'} disabled={!A} onClick={cmd.mergeDown}><Merge size={15} /></button>
        <button className="btn ghost icon sm" title="Flatten image" onClick={cmd.flatten}><LayersIcon size={15} /></button>
        <div className="grow" />
        <button className="btn ghost icon sm danger" title="Delete layer" disabled={!A} onClick={cmd.deleteLayer}><Trash2 size={15} /></button>
      </div>
    </div>
  )
}
