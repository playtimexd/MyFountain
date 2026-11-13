import { ChevronDown, ChevronRight, X, Plus } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { on } from '../../shared/bus'
import { AiPanel, AssetsPanel } from './AiPanel'
import { OptionsBar, Toolbar, TOOL_DEFS } from './Bars'
import { cmd } from './commands'
import Dialogs from './Dialogs'
import ColorRangePanel, { closeColorRange } from './ColorRange'
import { hooks } from './doc'
import { autosave, restoreAutosave } from './io'
import MenuBar from './MenuBar'
import { insertImageUrl, openFiles, pasteFromEvent, placeFiles } from './ops'
import { ColorPanel, HistoryPanel, LayersPanel } from './Panels'
import { PropsPanel } from './PropsPanel'
import { addDoc, curDoc, setCur, useStatus, useUI } from './store'
import { TOOLS } from './tools'
import * as storeMod from './store'
import * as toolsMod from './tools'
import * as opsMod from './ops'
import * as ioMod from './io'
import * as aiMod from './ai'
import type { ToolId } from './types'
import Viewport, { input } from './Viewport'
import ImageProjects, { imageSession } from './ImageProjects'
import './editor.css'

const PANELS: { id: string; title: string; grow: number; render: () => ReactNode }[] = [
  { id: 'color', title: 'Color', grow: 0, render: () => <ColorPanel /> },
  { id: 'layers', title: 'Layers', grow: 2, render: () => <LayersPanel /> },
  { id: 'props', title: 'Properties / Adjustments', grow: 2, render: () => <PropsPanel /> },
  { id: 'history', title: 'History', grow: 1, render: () => <HistoryPanel /> },
  { id: 'ai', title: 'AI', grow: 1, render: () => <AiPanel /> },
  { id: 'assets', title: 'Assets', grow: 1, render: () => <AssetsPanel /> },
]

function Dock() {
  const open = useUI((s) => s.panelsOpen)
  return (
    <div className="ie-dock">
      {PANELS.map((p) => (
        <div key={p.id} className={`ie-dp ${open[p.id] ? 'open' : ''}`} style={open[p.id] ? { flex: `${p.grow} 1 ${p.grow ? '0' : 'auto'}` } : undefined} data-panel={p.id}>
          <div className="dph" onClick={() => useUI.setState((s) => ({ panelsOpen: { ...s.panelsOpen, [p.id]: !s.panelsOpen[p.id] } }))}>
            {open[p.id] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{p.title}
          </div>
          {open[p.id] && <div className="dpb">{p.render()}</div>}
        </div>
      ))}
    </div>
  )
}

function DocTabs() {
  useUI((s) => s.rev)
  const docs = useUI((s) => s.docs), cur = useUI((s) => s.cur)
  return (
    <div className="ie-tabs">
      {docs.map((d) => (
        <div key={d.id} className={`dt ${d.id === cur ? 'on' : ''}`} onClick={() => setCur(d.id)} title={`${d.name} ${d.w}×${d.h}`}>
          <span className="n">{d.name}{d.dirty ? ' •' : ''}</span>
          <button onClick={(e) => { e.stopPropagation(); setCur(d.id); cmd.closeDoc() }}><X size={11} /></button>
        </div>
      ))}
      <button className="dt add" title="New document (Ctrl+N)" onClick={cmd.newDoc}><Plus size={13} /></button>
    </div>
  )
}

function StatusBar() {
  useUI((s) => s.rev)
  const st = useStatus()
  const d = curDoc()
  return (
    <div className="ie-status">
      {d ? (<>
        <span><button className="lnk" onClick={cmd.zoomOut}>−</button> {Math.round(d.view.zoom * 100)}% <button className="lnk" onClick={cmd.zoomIn}>＋</button>
          <button className="lnk" onClick={cmd.fit}>Fit</button><button className="lnk" onClick={cmd.zoom100}>100%</button></span>
        <span>{d.w} × {d.h} px</span>
        <span>{st.inDoc ? `${st.x}, ${st.y}` : '—'}</span>
        <span>{d.sel ? `Selection ${d.sel.bbox.w} × ${d.sel.bbox.h}` : 'No selection'}</span>
        <span>{d.layers.length} layers{d.active?.editMask ? ' · editing mask' : ''}</span>
        <span className="grow" />
        <span className="faint">History {(d.history.bytes / 1e6).toFixed(0)}MB</span>
      </>) : <span className="faint">No document</span>}
    </div>
  )
}

// dev-only handle for scripted verification
if (import.meta.env.DEV) Object.assign(window, { __ie: { store: storeMod, tools: toolsMod, ops: opsMod, io: ioMod, ai: aiMod, cmd } })

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)

export default function ImageEditor() {
  const root = useRef<HTMLDivElement>(null)
  const openIn = useRef<HTMLInputElement>(null)
  const placeIn = useRef<HTMLInputElement>(null)
  const visible = () => !!root.current && root.current.offsetParent !== null && !imageSession.use.getState().home // hidden tab or Projects screen

  // restore previous session
  useEffect(() => {
    let alive = true
    void restoreAutosave().then((r) => {
      if (!alive || !r || useUI.getState().docs.length) return
      r.docs.forEach((d) => addDoc(d))
      const target = r.docs[r.cur] ?? r.docs[0]
      if (target) setCur(target.id)
      hooks.toast(`Restored ${r.docs.length} document(s) from the last session`, 'ok')
    })
    return () => { alive = false }
  }, [])

  // autosave
  useEffect(() => {
    let last = ''
    const t = setInterval(() => {
      const { docs, cur } = useUI.getState()
      const sig = docs.map((d) => `${d.id}:${d.revision}`).join(',') + cur
      if (sig !== last) { last = sig; void autosave(docs, cur) }
    }, 3000)
    return () => clearInterval(t)
  }, [])

  // events from other modules
  useEffect(() => on('cutlab:open-image', ({ media }) => { void insertImageUrl(media.url, media.name.replace(/\.[^.]+$/, '')) }), [])
  useEffect(() => {
    const o = () => openIn.current?.click(), p = () => placeIn.current?.click()
    window.addEventListener('ie-open-file', o); window.addEventListener('ie-place-file', p)
    return () => { window.removeEventListener('ie-open-file', o); window.removeEventListener('ie-place-file', p) }
  }, [])
  useEffect(() => { // hooks: ui refresh when tool changes etc.
    const unsub = useUI.subscribe((s, p) => { if (s.tool !== p.tool) { const d = curDoc(); if (d) TOOLS[p.tool].cancel?.(d); hooks.render() } })
    return unsub
  }, [])

  // paste
  useEffect(() => {
    const h = (e: ClipboardEvent) => {
      if (!visible() || typing(e.target)) return
      e.preventDefault()
      void pasteFromEvent(e)
    }
    window.addEventListener('paste', h)
    return () => window.removeEventListener('paste', h)
  }, [])

  // keyboard
  useEffect(() => {
    const keyUp = (e: KeyboardEvent) => { if (e.code === 'Space') { input.space = false; hooks.render() } }
    const key = (e: KeyboardEvent) => {
      if (!visible() || useUI.getState().dialog) return
      if (typing(e.target)) return
      const d = curDoc()
      const ctrl = e.ctrlKey || e.metaKey, k = e.key.toLowerCase()
      const ui = useUI.getState()
      if (e.code === 'Space' && !ctrl) { input.space = true; e.preventDefault(); return }
      if (ui.colorRange && e.key === 'Escape') { e.preventDefault(); closeColorRange(false); return }
      if (ui.colorRange && e.key === 'Enter') { e.preventDefault(); closeColorRange(true); return }
      if (d && !ctrl && TOOLS[ui.tool].key?.(d, e)) { e.preventDefault(); return }
      const stop = () => e.preventDefault()
      if (ctrl) {
        if (k === 'z') { stop(); if (e.shiftKey) cmd.redo(); else cmd.undo(); return }
        if (k === 'y') { stop(); cmd.redo(); return }
        if (k === 'n') { stop(); if (e.shiftKey) cmd.newLayer(); else cmd.newDoc(); return }
        if (k === 'o') { stop(); cmd.openFile(); return }
        if (k === 's') { stop(); cmd.save(); return }
        if (k === 'e') { stop(); if (e.shiftKey) cmd.exportAs(); else cmd.mergeDown(); return }
        if (k === 'a') { stop(); cmd.selectAll(); return }
        if (k === 'd') { stop(); cmd.deselect(); return }
        if (k === 'i' && e.shiftKey) { stop(); cmd.invertSel(); return }
        if (k === 'j') { stop(); if (e.shiftKey) cmd.layerViaCut(); else cmd.layerViaCopy(); return }
        if (k === 'c') { stop(); cmd.copy(); return }
        if (k === 'x') { stop(); cmd.cut(); return }
        if (k === 't') { stop(); ui.setTool('move'); return }
        if (k === 'g' && e.altKey) { stop(); cmd.clipToBelow(); return }
        if (k === 'g' && !e.altKey) { stop(); if (e.shiftKey) cmd.ungroup(); else cmd.group(); return }
        if (k === '0') { stop(); cmd.fit(); return }
        if (k === 'r') { stop(); cmd.toggleRulers(); return }
        if (k === ';') { stop(); if (e.shiftKey) cmd.toggleSnap(); else cmd.toggleGuides(); return }
        if (k === '1') { stop(); cmd.zoom100(); return }
        if (k === '=' || k === '+') { stop(); cmd.zoomIn(); return }
        if (k === '-') { stop(); cmd.zoomOut(); return }
        if (k === ']') { stop(); cmd.moveUp(); return }
        if (k === '[') { stop(); cmd.moveDown(); return }
        if (e.key === 'Backspace') { stop(); cmd.fillBg(); return }
        return
      }
      if (e.altKey && e.key === 'Backspace') { stop(); cmd.fillFg(); return }
      if (e.key === 'Delete' || e.key === 'Backspace') { stop(); if (curDoc()?.sel) cmd.clear(); else cmd.deleteLayer(); return }
      if (e.altKey) return
      if (e.key === '[' || e.key === ']') {
        stop()
        const s = ui.opts.brushSize
        ui.setOpts({ brushSize: Math.max(1, Math.round(e.key === ']' ? s * 1.15 + 1 : s / 1.15 - 1)) })
        hooks.render(); return
      }
      if (e.key === 'Escape') { if (curDoc()?.sel) cmd.deselect(); return }
      if (k === 'x') { stop(); const f = ui.fg; ui.setFg(ui.bg); ui.setBg(f); return }
      if (k === 'd') { stop(); ui.setFg('#000000'); ui.setBg('#ffffff'); return }
      if (k === 'g' && ui.tool === 'gradient') { stop(); ui.setTool('bucket'); return }
      if (k === 'w' && (ui.tool === 'wand' || ui.tool === 'quicksel')) { stop(); ui.setTool(ui.tool === 'wand' ? 'quicksel' : 'wand'); return }
      if (k === 'm' && ui.tool === 'marquee') { stop(); ui.setOpts({ marquee: ui.opts.marquee === 'rect' ? 'ellipse' : 'rect' }); return }
      if (k === 'l' && ui.tool === 'lasso') { stop(); ui.setOpts({ lasso: ui.opts.lasso === 'free' ? 'poly' : 'free' }); return }
      const def = TOOL_DEFS.find((t) => t.key.toLowerCase() === k)
      if (def && !e.shiftKey) { stop(); ui.setTool(def.id as ToolId) }
    }
    window.addEventListener('keydown', key)
    window.addEventListener('keyup', keyUp)
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('keyup', keyUp) }
  }, [])

  return (
    <div className="ie-root" ref={root}>
      <MenuBar />
      <OptionsBar />
      <div className="ie-main">
        <Toolbar />
        <div className="ie-center">
          <DocTabs />
          <Viewport />
        </div>
        <Dock />
      </div>
      <StatusBar />
      <Dialogs />
      <ColorRangePanel />
      <Toast />
      <ImageProjects />
      <input ref={openIn} type="file" hidden multiple accept="image/*,.json" onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ''; void openFiles(f) }} />
      <input ref={placeIn} type="file" hidden multiple accept="image/*" onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ''; void placeFiles(f) }} />
    </div>
  )
}

function Toast() {
  const t = useUI((s) => s.toast)
  if (!t) return null
  return <div className={`ie-toast ${t.kind}`}>{t.msg}</div>
}
