import { useEffect, useRef } from 'react'
import { hooks } from './doc'
import { openFiles, placeFiles, newDocument } from './ops'
import { previewStroke } from './paint'
import { curDoc, openDialog, useStatus, useUI } from './store'
import { BRUSH_TOOLS, TOOLS, beginPan, endPan, liftOffset, movePan, samplePixel, type PtrEv } from './tools'
import { ctx2, mkCanvas } from './util'
import { afterDraw, fitView, toDoc, viewport, zoomAt } from './view'
import TextEditor, { startTextEdit } from './TextEditor'
import { RULER, commitGuides, drawGuides, drawRulers, snapAxis } from './guides'
import { toHex, uid } from './util'
import { Plus, FolderOpen } from 'lucide-react'

export const input = { space: false, mouse: { sx: -1, sy: -1, in: false } }
let checker: CanvasPattern | null = null
let tint: HTMLCanvasElement | null = null
let antOffset = 0
let gdrag: { id: string; axis: 'h' | 'v'; before: { id: string; axis: 'h' | 'v'; pos: number }[]; isNew: boolean } | null = null

export default function Viewport() {
  const wrap = useRef<HTMLDivElement>(null)
  const cv = useRef<HTMLCanvasElement>(null)
  const hasDoc = useUI((s) => s.docs.length > 0)
  const tool = useUI((s) => s.tool)

  useEffect(() => {
    const canvas = cv.current!, box = wrap.current!
    let raf = 0
    const draw = () => {
      raf = 0
      const d = curDoc()
      const dpr = window.devicePixelRatio || 1
      afterDraw.forEach((f) => f())
      const c = ctx2(canvas)
      c.setTransform(dpr, 0, 0, dpr, 0, 0)
      c.clearRect(0, 0, viewport.w, viewport.h)
      c.fillStyle = '#0b0c10'
      c.fillRect(0, 0, viewport.w, viewport.h)
      if (!d) return
      if (d.view.zoom === 0) { fitView(d); hooks.ui() }
      const { zoom: z, ox, oy } = d.view
      const comp = d.ensureComp()
      if (!checker) {
        const p = mkCanvas(16, 16), pc = ctx2(p)
        pc.fillStyle = '#ffffff'; pc.fillRect(0, 0, 16, 16)
        pc.fillStyle = '#d4d6dc'; pc.fillRect(0, 0, 8, 8); pc.fillRect(8, 8, 8, 8)
        checker = c.createPattern(p, 'repeat')
      }
      const dw = d.w * z, dh = d.h * z
      c.save()
      c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = 24
      c.fillStyle = '#fff'; c.fillRect(ox, oy, dw, dh)
      c.restore()
      // visible doc rect
      const vx0 = Math.max(0, Math.floor(-ox / z)), vy0 = Math.max(0, Math.floor(-oy / z))
      const vx1 = Math.min(d.w, Math.ceil((viewport.w - ox) / z)), vy1 = Math.min(d.h, Math.ceil((viewport.h - oy) / z))
      if (vx1 > vx0 && vy1 > vy0) {
        c.save()
        c.beginPath(); c.rect(ox + vx0 * z, oy + vy0 * z, (vx1 - vx0) * z, (vy1 - vy0) * z); c.clip()
        if (checker) { c.fillStyle = checker; c.fillRect(ox + vx0 * z, oy + vy0 * z, (vx1 - vx0) * z, (vy1 - vy0) * z) }
        c.imageSmoothingEnabled = z < 1
        c.imageSmoothingQuality = 'high'
        c.drawImage(comp, vx0, vy0, vx1 - vx0, vy1 - vy0, ox + vx0 * z, oy + vy0 * z, (vx1 - vx0) * z, (vy1 - vy0) * z)
        // stroke previews (heal / AI marking brush)
        if (previewStroke && previewStroke.doc === d) {
          if (!tint || tint.width !== d.w || tint.height !== d.h) tint = mkCanvas(d.w, d.h)
          const t = ctx2(tint)
          t.globalCompositeOperation = 'copy'; t.drawImage(d.stroke, 0, 0)
          t.globalCompositeOperation = 'source-in'; t.fillStyle = previewStroke.color; t.fillRect(0, 0, d.w, d.h)
          c.globalAlpha = previewStroke.alpha
          c.drawImage(tint, vx0, vy0, vx1 - vx0, vy1 - vy0, ox + vx0 * z, oy + vy0 * z, (vx1 - vx0) * z, (vy1 - vy0) * z)
          c.globalAlpha = 1
        }
        // mask editing tint: show hidden area in red
        const L = d.active
        if (L && L.editMask && L.mask) {
          if (!tint || tint.width !== d.w || tint.height !== d.h) tint = mkCanvas(d.w, d.h)
          const t = ctx2(tint)
          t.globalCompositeOperation = 'copy'; t.fillStyle = 'rgba(255,40,60,0.9)'; t.fillRect(0, 0, d.w, d.h)
          t.globalCompositeOperation = 'destination-out'; t.drawImage(L.mask.canvas, 0, 0)
          c.globalAlpha = 0.4
          c.drawImage(tint, vx0, vy0, vx1 - vx0, vy1 - vy0, ox + vx0 * z, oy + vy0 * z, (vx1 - vx0) * z, (vy1 - vy0) * z)
          c.globalAlpha = 1
        }
        c.restore()
      }
      c.strokeStyle = 'rgba(255,255,255,.18)'; c.lineWidth = 1; c.strokeRect(ox - 0.5, oy - 0.5, dw + 1, dh + 1)
      // selection marching ants
      if (d.sel?.ants) {
        c.save()
        c.translate(ox, oy); c.scale(z, z)
        if (liftOffset) c.translate(liftOffset.dx, liftOffset.dy)
        c.lineWidth = 1 / z
        c.strokeStyle = '#000'; c.setLineDash([]); c.stroke(d.sel.ants)
        if (d.sel.antsCount < 250000) { c.strokeStyle = '#fff'; c.setLineDash([4 / z, 4 / z]); c.lineDashOffset = -antOffset / z; c.stroke(d.sel.ants) }
        c.restore()
      }
      const tl = useUI.getState().tool
      const ust = useUI.getState()
      if (ust.showGuides && d.guides.length) drawGuides(c, d, viewport.w, viewport.h, ust.rulers)
      TOOLS[tl].overlay?.(c, d)
      // brush cursor
      if (BRUSH_TOOLS.includes(tl) && input.mouse.in && !input.space) {
        const r = (useUI.getState().opts.brushSize * z) / 2
        c.save()
        c.lineWidth = 1
        c.strokeStyle = 'rgba(0,0,0,.8)'; c.beginPath(); c.arc(input.mouse.sx, input.mouse.sy, Math.max(r, 1), 0, Math.PI * 2); c.stroke()
        c.strokeStyle = 'rgba(255,255,255,.9)'; c.beginPath(); c.arc(input.mouse.sx, input.mouse.sy, Math.max(r - 1, 0.5), 0, Math.PI * 2); c.stroke()
        c.beginPath(); c.moveTo(input.mouse.sx - 3, input.mouse.sy); c.lineTo(input.mouse.sx + 3, input.mouse.sy); c.moveTo(input.mouse.sx, input.mouse.sy - 3); c.lineTo(input.mouse.sx, input.mouse.sy + 3); c.stroke()
        c.restore()
      }
      if (ust.rulers) drawRulers(c, d, viewport.w, viewport.h, input.mouse)
      if (gdrag) {
        const g = d.guides.find((x) => x.id === gdrag!.id)
        if (g) { c.save(); c.font = '11px sans-serif'; c.fillStyle = '#2fd4ff'; c.fillText(`${g.axis === 'v' ? 'X' : 'Y'}: ${g.pos}`, input.mouse.sx + 12, input.mouse.sy - 8); c.restore() }
      }
    }
    const schedule = () => { if (!raf) raf = requestAnimationFrame(draw) }
    hooks.render = schedule
    const ro = new ResizeObserver(() => {
      const r = box.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      viewport.w = Math.max(10, r.width); viewport.h = Math.max(10, r.height)
      canvas.width = Math.round(viewport.w * dpr); canvas.height = Math.round(viewport.h * dpr)
      canvas.style.width = viewport.w + 'px'; canvas.style.height = viewport.h + 'px'
      const d = curDoc()
      if (d && d.view.fitted) fitView(d)
      schedule()
    })
    ro.observe(box)
    const ants = window.setInterval(() => { if (curDoc()?.sel && box.offsetParent) { antOffset = (antOffset + 1) % 8; schedule() } }, 110)
    schedule()
    return () => { ro.disconnect(); clearInterval(ants); cancelAnimationFrame(raf); hooks.render = () => {} }
  }, [])

  // pointer handling -----------------------------------------------------------
  useEffect(() => {
    const box = wrap.current!
    let active = false, panning = false, buttonDown = -1
    const mk = (e: PointerEvent): PtrEv | null => {
      const d = curDoc()
      if (!d) return null
      const r = box.getBoundingClientRect()
      const sx = e.clientX - r.left, sy = e.clientY - r.top
      const [x, y] = toDoc(d, sx, sy)
      return { x, y, sx, sy, shift: e.shiftKey, alt: e.altKey, ctrl: e.ctrlKey || e.metaKey, pressure: e.pressure, button: e.button }
    }
    const down = (e: PointerEvent) => {
      const d = curDoc()
      if (!d) return
      if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) document.activeElement.blur()
      const ev = mk(e)!
      try { box.setPointerCapture(e.pointerId) } catch { /* synthetic pointer */ }
      buttonDown = e.button
      const ust = useUI.getState()
      if (e.button === 0 && !input.space) {
        let axis: 'h' | 'v' | null = null, id = ''
        if (ust.rulers && ev.sy < RULER && ev.sx >= RULER) axis = 'h'
        else if (ust.rulers && ev.sx < RULER && ev.sy >= RULER) axis = 'v'
        const near = ust.showGuides && ust.tool === 'move' ? d.guides.find((g) => (g.axis === 'v' ? Math.abs(ev.sx - (d.view.ox + g.pos * d.view.zoom)) : Math.abs(ev.sy - (d.view.oy + g.pos * d.view.zoom))) < 4) : undefined
        const before = d.guides.map((g) => ({ ...g }))
        if (axis) { id = uid(); d.guides = [...d.guides, { id, axis, pos: Math.round(axis === 'h' ? ev.y : ev.x) }]; gdrag = { id, axis, before, isNew: true } }
        else if (near) gdrag = { id: near.id, axis: near.axis, before, isNew: false }
        if (gdrag) { hooks.render(); return }
      }
      if (e.button === 1 || e.button === 2 || input.space || useUI.getState().tool === 'hand') {
        panning = true; beginPan(d, ev.sx, ev.sy); box.style.cursor = 'grabbing'; e.preventDefault(); return
      }
      if (e.button !== 0) return
      if (useUI.getState().colorRange) {
        const p = samplePixel(d, ev.x, ev.y, useUI.getState().colorRange!.all)
        if (p) useUI.setState((s) => ({ colorRange: s.colorRange && { ...s.colorRange, color: toHex({ r: p.r, g: p.g, b: p.b, a: 1 }, false) } }))
        return
      }
      active = true
      TOOLS[useUI.getState().tool].down?.(d, ev)
    }
    const move = (e: PointerEvent) => {
      const d = curDoc()
      if (!d) return
      const ev = mk(e)!
      input.mouse = { sx: ev.sx, sy: ev.sy, in: true }
      useStatus.setState({ x: Math.floor(ev.x), y: Math.floor(ev.y), inDoc: ev.x >= 0 && ev.y >= 0 && ev.x < d.w && ev.y < d.h })
      const tool = TOOLS[useUI.getState().tool]
      if (gdrag) {
        const g = d.guides.find((x) => x.id === gdrag!.id)
        if (g) { g.pos = Math.round(snapAxis(d, g.axis === 'h' ? 'y' : 'x', g.axis === 'h' ? ev.y : ev.x, g.id)); hooks.render() }
        return
      }
      if (panning) { movePan(d, ev.sx, ev.sy); return }
      if (active) {
        const co = (e as PointerEvent & { getCoalescedEvents?: () => PointerEvent[] }).getCoalescedEvents?.() ?? []
        if (co.length > 1 && ['brush', 'eraser', 'clone', 'heal', 'aibrush', 'retouch', 'tone', 'lasso', 'quicksel'].includes(useUI.getState().tool)) {
          for (const ce of co) { const cev = mk(ce); if (cev) tool.move?.(d, cev) }
        } else tool.move?.(d, ev)
      } else {
        const gh = useUI.getState().showGuides && useUI.getState().tool === 'move' ? d.guides.find((g) => (g.axis === 'v' ? Math.abs(ev.sx - (d.view.ox + g.pos * d.view.zoom)) : Math.abs(ev.sy - (d.view.oy + g.pos * d.view.zoom))) < 4) : undefined
        const cur = input.space ? 'grab' : gh ? (gh.axis === 'v' ? 'ew-resize' : 'ns-resize') : tool.hover?.(d, ev) || tool.cursor
        box.style.cursor = cur
        if (BRUSH_TOOLS.includes(useUI.getState().tool)) hooks.render()
      }
    }
    const up = (e: PointerEvent) => {
      const d = curDoc()
      try { if (box.hasPointerCapture(e.pointerId)) box.releasePointerCapture(e.pointerId) } catch { /* ignore */ }
      if (gdrag && d) {
        const gd = gdrag
        gdrag = null
        const r = box.getBoundingClientRect(), sx = e.clientX - r.left, sy = e.clientY - r.top
        const outside = (useUI.getState().rulers && (gd.axis === 'h' ? sy < RULER : sx < RULER)) || sx < 0 || sy < 0 || sx > viewport.w || sy > viewport.h
        if (outside) d.guides = d.guides.filter((g) => g.id !== gd.id)
        commitGuides(d, gd.before, gd.isNew ? 'New guide' : outside ? 'Delete guide' : 'Move guide')
        return
      }
      if (panning) { panning = false; endPan(); box.style.cursor = ''; return }
      if (!active) return
      active = false
      const ev = mk(e)
      if (d && ev) TOOLS[useUI.getState().tool].up?.(d, ev)
      void buttonDown
    }
    const leave = () => { input.mouse.in = false; useStatus.setState({ inDoc: false }); hooks.render() }
    const wheel = (e: WheelEvent) => {
      const d = curDoc()
      if (!d) return
      e.preventDefault()
      const r = box.getBoundingClientRect()
      const k = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016))
      zoomAt(d, e.clientX - r.left, e.clientY - r.top, d.view.zoom * k)
      hooks.render(); hooks.ui()
    }
    const ctx = (e: Event) => e.preventDefault()
    const dbl = () => {
      const d = curDoc()
      if (!d || useUI.getState().tool !== 'move') return
      const L = d.active
      if (L?.type === 'text') { useUI.setState((s) => ({ panelsOpen: { ...s.panelsOpen, props: true } })); startTextEdit(L.id) }
    }
    box.addEventListener('pointerdown', down)
    box.addEventListener('pointermove', move)
    box.addEventListener('pointerup', up)
    box.addEventListener('pointercancel', up)
    box.addEventListener('pointerleave', leave)
    box.addEventListener('wheel', wheel, { passive: false })
    box.addEventListener('contextmenu', ctx)
    box.addEventListener('dblclick', dbl)
    return () => {
      box.removeEventListener('pointerdown', down); box.removeEventListener('pointermove', move); box.removeEventListener('pointerup', up)
      box.removeEventListener('pointercancel', up); box.removeEventListener('pointerleave', leave); box.removeEventListener('wheel', wheel)
      box.removeEventListener('contextmenu', ctx); box.removeEventListener('dblclick', dbl)
    }
  }, [])

  useEffect(() => { if (wrap.current) wrap.current.style.cursor = TOOLS[tool].cursor; hooks.render() }, [tool])

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    const files = Array.from(e.dataTransfer.files)
    if (!files.length) return
    const d = curDoc()
    const imgs = files.filter((f) => f.type.startsWith('image/')), other = files.filter((f) => !f.type.startsWith('image/'))
    if (other.length) await openFiles(other)
    if (imgs.length) {
      if (!d) await openFiles(imgs)
      else {
        const r = wrap.current!.getBoundingClientRect()
        const [x, y] = toDoc(d, e.clientX - r.left, e.clientY - r.top)
        await placeFiles(imgs, { x, y })
      }
    }
  }

  return (
    <div ref={wrap} className="ie-viewport" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <canvas ref={cv} />
      <TextEditor />
      {!hasDoc && (
        <div className="ie-empty">
          <div className="big">Image Editor</div>
          <div className="faint">Create a document, open an image, or drag an image in</div>
          <div className="row" style={{ justifyContent: 'center', marginTop: 14 }}>
            <button className="btn primary" onClick={() => openDialog({ kind: 'new' })}><Plus size={14} />New Document</button>
            <button className="btn" onClick={() => window.dispatchEvent(new Event('ie-open-file'))}><FolderOpen size={14} />Open Image…</button>
            <button className="btn ghost" onClick={() => newDocument(1920, 1080, 'white')}>Quick New 1080p</button>
          </div>
        </div>
      )}
    </div>
  )
}
