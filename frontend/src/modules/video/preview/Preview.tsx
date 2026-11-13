import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { Film, Maximize2, Pause, Play, SkipBack, SkipForward, StepBack, StepForward } from 'lucide-react'
import { useProject, newGesture, selectedClips } from '../store'
import { player } from '../engine/player'
import { clock } from '../engine/clock'
import { hitBox } from '../engine/compositor'
import type { ClipBox } from '../engine/compositor'
import { clipDuration, projectDuration } from '../../../types/project'
import type { Clip } from '../../../types/project'
import { IconBtn, fmtTC } from '../ui'
import { useMedia } from '../data'
import { clipTraits } from '../clipTypes'

const ZOOMS = [{ v: 0, l: 'Fit' }, { v: 0.5, l: '50%' }, { v: 1.5, l: '150%' }, { v: 2, l: '200%' }, { v: 3, l: '300%' }]

export default function Preview() {
  const project = useProject((s) => s.project)
  const playing = useProject((s) => s.playing)
  const stageRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const timeRef = useRef<HTMLSpanElement>(null)
  const [stageSize, setStageSize] = useState({ w: 600, h: 340 })
  const [zoom, setZoom] = useState(0)
  const dur = projectDuration(project)

  useEffect(() => {
    const el = stageRef.current!
    const ro = new ResizeObserver(() => setStageSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el); setStageSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  const fit = Math.min(Math.max(50, stageSize.w - 32) / project.width, Math.max(50, stageSize.h - 32) / project.height)
  const scale = zoom === 0 ? fit : fit * zoom
  const dw = Math.round(project.width * scale), dh = Math.round(project.height * scale)

  useEffect(() => {
    const cv = canvasRef.current!
    player.attach(cv)
    return () => player.detach(cv)
  }, [])
  useEffect(() => {
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const res = Math.min(1, 1600 / Math.max(project.width, project.height), (dw * dpr) / project.width)
    player.resize(project.width * res, project.height * res)
  }, [dw, dh, project.width, project.height])

  useEffect(() => {
    const upd = (t: number) => {
      if (timeRef.current) timeRef.current.textContent = fmtTC(t, useProject.getState().project.fps)
    }
    upd(clock.time)
    return clock.subscribe(upd)
  }, [])

  const step = (n: number) => { player.pause(); player.seek(clock.time + n / project.fps) }

  return (
    <div className="ve-preview">
      <div className="ve-stage" ref={stageRef} style={{ overflow: zoom > 1 ? 'auto' : 'hidden' }}>
        <div className="ve-canvas-box" style={{ width: dw, height: dh }}>
          <canvas ref={canvasRef} style={{ width: dw, height: dh }} />
          <Overlay dw={dw} dh={dh} />
        </div>
        {dur <= 0 && (
          <div className="ve-stage-empty">
            <Film size={22} />
            <b>Nothing on the timeline yet</b>
            <span>Import or drop media on the left, then drag it to the timeline</span>
          </div>
        )}
      </div>
      <div className="ve-controls">
        <span className="ve-time"><span ref={timeRef}>00:00:00</span><span className="faint"> / {fmtTC(dur, project.fps)}</span></span>
        <div className="grow row" style={{ justifyContent: 'center', gap: 4 }}>
          <IconBtn title="Go to start (Home)" onClick={() => player.seek(0)}><SkipBack size={15} /></IconBtn>
          <IconBtn title="Previous frame (←)" onClick={() => step(-1)}><StepBack size={15} /></IconBtn>
          <button className="ve-play" onClick={() => player.toggle()} title="Play/Pause (Space)">{playing ? <Pause size={18} /> : <Play size={18} />}</button>
          <IconBtn title="Next frame (→)" onClick={() => step(1)}><StepForward size={15} /></IconBtn>
          <IconBtn title="Go to end (End)" onClick={() => player.seek(dur)}><SkipForward size={15} /></IconBtn>
        </div>
        <select className="ve-select" value={zoom} onChange={(e) => setZoom(parseFloat(e.target.value))} title="Preview zoom">
          {ZOOMS.map((z) => <option key={z.v} value={z.v}>{z.l}</option>)}
        </select>
        <IconBtn title="Fullscreen preview" onClick={() => stageRef.current?.requestFullscreen?.()}><Maximize2 size={15} /></IconBtn>
      </div>
    </div>
  )
}

type Drag =
  | { kind: 'move'; gid: string; x0: number; y0: number; sx: number; sy: number }
  | { kind: 'scale'; gid: string; s0: number; d0: number; cx: number; cy: number }
  | { kind: 'rotate'; gid: string; cx: number; cy: number }

const Overlay = memo(function Overlay({ dw, dh }: { dw: number; dh: number }) {
  const project = useProject((s) => s.project)
  const selection = useProject((s) => s.selection)
  useProject((s) => s.playhead)
  useMedia((s) => s.byId)
  const ref = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const [, force] = useState(0)

  const sel = selectedClips({ project, selection }).filter((c) => clipTraits(c.type).visual)
  const clip: Clip | undefined = sel[0]
  const t = clock.time
  const k = dw / project.width

  const boxOf = (c: Clip): ClipBox | null => {
    const lt = t - c.start
    if (lt < 0 || lt >= clipDuration(c)) return null
    return player.compositor.box(c, project.width, project.height, lt, player.deps)
  }
  const box = clip ? boxOf(clip) : null

  const hitTest = useCallback((px: number, py: number): Clip | null => {
    const p = useProject.getState().project
    const tt = clock.time
    for (let ti = p.tracks.length - 1; ti >= 0; ti--) {
      const tr = p.tracks[ti]
      if (tr.hidden || tr.kind === 'audio') continue
      for (let ci = tr.clips.length - 1; ci >= 0; ci--) {
        const c = tr.clips[ci]
        if (!clipTraits(c.type).visual) continue
        const lt = tt - c.start
        if (lt < 0 || lt >= clipDuration(c)) continue
        const b = player.compositor.box(c, p.width, p.height, lt, player.deps)
        if (b && hitBox(b, px, py)) return c
      }
    }
    return null
  }, [])

  const toProject = (e: { clientX: number; clientY: number }) => {
    const r = ref.current!.getBoundingClientRect()
    return { x: ((e.clientX - r.left) / r.width) * project.width, y: ((e.clientY - r.top) / r.height) * project.height }
  }

  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    const pt = toProject(e)
    let target = clip && box && hitBox(box, pt.x, pt.y) ? clip : hitTest(pt.x, pt.y)
    if (!target) { useProject.getState().select([]); return }
    const lock = useProject.getState().project.tracks.find((tr) => tr.clips.some((c) => c.id === target!.id))?.locked
    if (!sel.some((c) => c.id === target!.id) || sel.length !== 1) useProject.getState().select([target.id])
    if (lock) return
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    drag.current = { kind: 'move', gid: newGesture(), x0: target.transform.x, y0: target.transform.y, sx: e.clientX, sy: e.clientY }
    force((n) => n + 1)
  }
  const startHandle = (kind: 'scale' | 'rotate') => (e: React.PointerEvent) => {
    if (!clip || !box) return
    e.stopPropagation()
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    const r = ref.current!.getBoundingClientRect()
    const cx = r.left + (box.cx / project.width) * r.width, cy = r.top + (box.cy / project.height) * r.height
    if (kind === 'scale') drag.current = { kind, gid: newGesture(), s0: clip.transform.scale, d0: Math.max(4, Math.hypot(e.clientX - cx, e.clientY - cy)), cx, cy }
    else drag.current = { kind, gid: newGesture(), cx, cy }
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d || !clip) return
    const st = useProject.getState()
    if (d.kind === 'move') {
      let x = d.x0 + (e.clientX - d.sx) / dw, y = d.y0 + (e.clientY - d.sy) / dh
      if (!e.altKey) { if (Math.abs(x) < 0.012) x = 0; if (Math.abs(y) < 0.012) y = 0 }
      st.setTransform(st.selection, { x: +x.toFixed(4), y: +y.toFixed(4) }, d.gid)
    } else if (d.kind === 'scale') {
      const dist = Math.hypot(e.clientX - d.cx, e.clientY - d.cy)
      st.setTransform(st.selection, { scale: +Math.min(10, Math.max(0.02, d.s0 * (dist / d.d0))).toFixed(4) }, d.gid)
    } else {
      let a = (Math.atan2(e.clientY - d.cy, e.clientX - d.cx) * 180) / Math.PI + 90
      a = ((a + 540) % 360) - 180
      const snap = e.shiftKey ? 15 : 3
      const r = Math.round(a / 45) * 45
      if (e.shiftKey) a = Math.round(a / snap) * snap
      else if (Math.abs(a - r) < snap) a = r
      st.setTransform(st.selection, { rotation: +a.toFixed(1) }, d.gid)
    }
  }
  const onUp = () => { drag.current = null }

  const sc = box ? { l: (box.cx - box.w / 2) * k, t: (box.cy - box.h / 2) * k, w: box.w * k, h: box.h * k } : null
  const locked = clip ? project.tracks.find((tr) => tr.clips.some((c) => c.id === clip.id))?.locked : false
  return (
    <div ref={ref} className="ve-overlay" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
      {sc && box && !locked && (
        <div className="ve-sel" style={{ left: sc.l, top: sc.t, width: sc.w, height: sc.h, transform: `rotate(${box.rot}deg)` }}>
          {(['nw', 'ne', 'sw', 'se'] as const).map((h) => (
            <i key={h} className={`ve-h ve-h-${h}`} onPointerDown={startHandle('scale')} />
          ))}
          <i className="ve-rot-line" />
          <i className="ve-h ve-h-rot" title="Rotate" onPointerDown={startHandle('rotate')} />
        </div>
      )}
    </div>
  )
})
