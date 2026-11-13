import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { AudioLines, Copy, Eye, EyeOff, GripVertical, Lock, Magnet, Minus, Plus, Scissors, Trash2, Unlock, Volume2, VolumeX, X, ChevronsRight, ZoomIn } from 'lucide-react'
import { useProject, newGesture } from '../store'
import { useMedia } from '../data'
import { player } from '../engine/player'
import { clock } from '../engine/clock'
import { clipDuration, clipEnd, projectDuration } from '../../../types/project'
import type { Clip, Project, Track, TrackKind } from '../../../types/project'
import { createTrack, kindForType, nearestFree, EPS } from '../ops'
import { ClipView } from './ClipView'
import ClipMenu from './ClipMenu'
import { clipTraits } from '../clipTypes'
import { DND_MIME, hasFiles, uploadFiles } from '../actions'
import type { DndPayload } from '../actions'
import { IconBtn } from '../ui'

export const HEADER_W = 150
const RULER_H = 28
const NEW_ZONE_H = 64
const laneH = (k: TrackKind) => (k === 'video' ? 58 : k === 'audio' ? 44 : 34)

function displayRows(p: Project): Track[] {
  const rev = [...p.tracks].reverse()
  return [...rev.filter((t) => t.kind === 'text'), ...rev.filter((t) => t.kind === 'video'), ...rev.filter((t) => t.kind === 'audio')]
}

// ----------------------------------------------------------------------------------------------- ruler
const STEPS = [0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]
const Ruler = memo(function Ruler({ zoom, width, fps }: { zoom: number; width: number; fps: number }) {
  const step = STEPS.find((s) => s * zoom >= 80) ?? 1800
  const sub = step >= 5 ? 5 : step >= 1 ? 4 : step >= 0.2 ? 5 : 4
  const ticks: React.ReactNode[] = []
  const total = width / zoom
  const subStep = step / sub
  const n = Math.min(4000, Math.ceil(total / subStep))
  for (let i = 0; i <= n; i++) {
    const t = i * subStep
    const major = i % sub === 0
    ticks.push(
      <i key={i} className={major ? 'maj' : 'min'} style={{ left: t * zoom }}>
        {major && <span>{fmtRuler(t, step, fps)}</span>}
      </i>,
    )
  }
  return <>{ticks}</>
})
function fmtRuler(t: number, step: number, fps: number) {
  const m = Math.floor(t / 60), s = t % 60
  if (step < 1) return `${m ? m + ':' : ''}${s.toFixed(step < 0.1 ? 2 : 1).padStart(m ? 4 : 1, '0')}s`
  void fps
  return m ? `${m}:${String(Math.floor(s)).padStart(2, '0')}` : `${Math.floor(s)}s`
}

// ----------------------------------------------------------------------------------------------- track header
const TrackHeader = memo(function TrackHeader({ track, height, onGrip, dragging }: {
  track: Track; height: number; onGrip: (e: React.PointerEvent, id: string) => void; dragging: boolean
}) {
  const upd = useProject.getState().updateTrack
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(track.name)
  const commit = () => { setEditing(false); const n = name.trim(); if (n && n !== track.name) upd(track.id, { name: n }) }
  return (
    <div className={`ve-th ${dragging ? 'dragging' : ''}`} style={{ height }}>
      <span className="ve-grip" title="Drag to reorder tracks (higher = on top)" onPointerDown={(e) => onGrip(e, track.id)}><GripVertical size={12} /></span>
      {editing ? (
        <input className="ve-th-edit" autoFocus value={name} onChange={(e) => setName(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(); else if (e.key === 'Escape') { setName(track.name); setEditing(false) } e.stopPropagation() }} />
      ) : (
        <span className="ve-th-name" title={`${track.name} (double-click to rename)`} onDoubleClick={() => { setName(track.name); setEditing(true) }}>{track.name}</span>
      )}
      {track.kind !== 'text' && (
        <IconBtn title={track.muted ? 'Unmute' : 'Mute'} active={track.muted} onClick={() => upd(track.id, { muted: !track.muted })}>
          {track.muted ? <VolumeX size={12} /> : <Volume2 size={12} />}
        </IconBtn>
      )}
      {track.kind !== 'audio' && (
        <IconBtn title={track.hidden ? 'Show' : 'Hide'} active={track.hidden} onClick={() => upd(track.id, { hidden: !track.hidden })}>
          {track.hidden ? <EyeOff size={12} /> : <Eye size={12} />}
        </IconBtn>
      )}
      <IconBtn title={track.locked ? 'Unlock' : 'Lock'} active={track.locked} onClick={() => upd(track.id, { locked: !track.locked })}>
        {track.locked ? <Lock size={12} /> : <Unlock size={12} />}
      </IconBtn>
      <button className="ve-th-x" title="Delete track" onClick={() => useProject.getState().removeTrack(track.id)}><X size={11} /></button>
    </div>
  )
})

// ----------------------------------------------------------------------------------------------- drag state
type MoveDrag = {
  mode: 'move'; gid: string; ids: string[]; primary: string; px0: number; py0: number; moved: boolean
  orig: Map<string, { start: number; trackId: string }>; dur: number; kind: TrackKind; pts: number[]; newZone: boolean
}
type TrimDrag = {
  mode: 'trimL' | 'trimR'; gid: string; clip: Clip; trackId: string; px0: number; pts: number[]; ripple: boolean
  later: { id: string; start: number }[]; prevEnd: number; nextStart: number; mediaDur?: number
}
type Drag = MoveDrag | TrimDrag

export default function Timeline() {
  const project = useProject((s) => s.project)
  const selection = useProject((s) => s.selection)
  const zoom = useProject((s) => s.zoom)
  const magnet = useProject((s) => s.magnet)
  const ripple = useProject((s) => s.ripple)
  const byId = useMedia((s) => s.byId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)
  const phRef = useRef<HTMLDivElement>(null)
  const snapRef = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const pendingScroll = useRef<number | null>(null)
  const [newZone, setNewZone] = useState(false)
  const [dropHint, setDropHint] = useState(false)
  const [dragTrack, setDragTrack] = useState<string | null>(null)
  const marqueeRef = useRef<HTMLDivElement>(null)

  const rows = useMemo(() => displayRows(project), [project])
  const tops = useMemo(() => { let y = RULER_H; return rows.map((r) => { const t = y; y += laneH(r.kind); return t }) }, [rows])
  const lanesBottom = rows.length ? tops[rows.length - 1] + laneH(rows[rows.length - 1].kind) : RULER_H
  const dur = projectDuration(project)
  const contentW = Math.max(dur + 12, 40) * zoom
  const innerH = lanesBottom + NEW_ZONE_H

  // keep refs of latest values for window-level handlers
  const live = useRef({ zoom, rows, tops, project, magnet, ripple, lanesBottom })
  live.current = { zoom, rows, tops, project, magnet, ripple, lanesBottom }

  // ---- playhead DOM updates (no React re-render per frame)
  const placePlayhead = useCallback((t: number) => {
    const ph = phRef.current
    if (!ph) return
    ph.style.transform = `translateX(${HEADER_W + t * live.current.zoom}px)`
    const sc = scrollRef.current
    if (sc && clock.playing) {
      const x = HEADER_W + t * live.current.zoom
      const vw = sc.clientWidth
      if (x - sc.scrollLeft > vw - 40 || x - sc.scrollLeft < HEADER_W) sc.scrollLeft = Math.max(0, x - HEADER_W - 80)
    }
  }, [])
  useEffect(() => { placePlayhead(clock.time); return clock.subscribe(placePlayhead) }, [placePlayhead])
  useLayoutEffect(() => {
    placePlayhead(clock.time)
    if (pendingScroll.current != null && scrollRef.current) { scrollRef.current.scrollLeft = pendingScroll.current; pendingScroll.current = null }
  }, [zoom, placePlayhead])

  // ---- ctrl+wheel zoom
  useEffect(() => {
    const el = scrollRef.current!
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      e.preventDefault()
      const st = useProject.getState()
      const rect = el.getBoundingClientRect()
      const vx = e.clientX - rect.left
      const tUnder = (el.scrollLeft + vx - HEADER_W) / st.zoom
      const nz = Math.min(600, Math.max(4, st.zoom * Math.exp(-e.deltaY * 0.0015)))
      pendingScroll.current = Math.max(0, tUnder * nz + HEADER_W - vx)
      st.setZoom(nz)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const rowAtY = (clientY: number): { row: Track | null; below: boolean } => {
    const r = innerRef.current!.getBoundingClientRect()
    const y = clientY - r.top
    const { rows, tops } = live.current
    for (let i = 0; i < rows.length; i++) if (y >= tops[i] && y < tops[i] + laneH(rows[i].kind)) return { row: rows[i], below: false }
    return { row: null, below: y >= live.current.lanesBottom }
  }
  const timeAtX = (clientX: number) => {
    const r = innerRef.current!.getBoundingClientRect()
    return Math.max(0, (clientX - r.left - HEADER_W) / live.current.zoom)
  }

  const showSnap = (t: number | null) => {
    const el = snapRef.current
    if (!el) return
    if (t == null) el.style.display = 'none'
    else { el.style.display = 'block'; el.style.transform = `translateX(${HEADER_W + t * live.current.zoom}px)` }
  }

  const snapPoints = (exclude: Set<string>) => {
    const pts = [0, clock.time]
    for (const t of live.current.project.tracks) for (const c of t.clips) if (!exclude.has(c.id)) pts.push(c.start, clipEnd(c))
    return pts
  }
  const nearestPt = (pts: number[], t: number, thr: number): number | null => {
    let best: number | null = null, bd = thr
    for (const p of pts) { const d = Math.abs(p - t); if (d < bd) { bd = d; best = p } }
    return best
  }

  // ---- window-level drag handling
  const onWinMove = useCallback((e: PointerEvent) => {
    const d = drag.current
    if (!d) return
    const { zoom, project: p, magnet, ripple } = live.current
    const st = useProject.getState()
    const thr = 8 / zoom
    if (d.mode === 'move') {
      if (!d.moved && Math.hypot(e.clientX - d.px0, e.clientY - d.py0) < 3) return
      d.moved = true
      let dt = (e.clientX - d.px0) / zoom
      const o = d.orig.get(d.primary)!
      const minStart = Math.min(...[...d.orig.values()].map((v) => v.start))
      dt = Math.max(dt, -minStart)
      let snapped: number | null = null
      if (magnet && !e.altKey) {
        const s = o.start + dt, en = s + d.dur
        const a = nearestPt(d.pts, s, thr), b = nearestPt(d.pts, en, thr)
        if (a != null && (b == null || Math.abs(a - s) <= Math.abs(b - en))) { dt = a - o.start; snapped = a }
        else if (b != null) { dt = b - d.dur - o.start; snapped = b }
        if (snapped != null && o.start + dt < 0) { dt = -o.start; snapped = 0 }
      }
      showSnap(snapped)
      const { row, below } = rowAtY(e.clientY)
      const single = d.ids.length === 1
      const newZ = single && below
      if (newZ !== d.newZone) { d.newZone = newZ; setNewZone(newZ) }
      const destId = single && row && row.kind === d.kind && !row.locked ? row.id : single && newZ ? o.trackId : o.trackId
      st.mutate((proj) => {
        const idSet = new Set(d.ids)
        const targets = new Map<string, { start: number; trackId: string }>()
        for (const id of d.ids) {
          const og = d.orig.get(id)!
          let start = Math.max(0, og.start + dt)
          let tid = og.trackId
          if (single) {
            tid = destId
            const dest = proj.tracks.find((t) => t.id === tid)
            if (dest && !newZ) start = nearestFree(dest, start, d.dur, idSet)
          }
          targets.set(id, { start, trackId: tid })
        }
        return placeClips(proj, targets)
      }, d.gid)
      return
    }
    // trim
    const dt = (e.clientX - d.px0) / zoom
    const c = d.clip
    const spd = c.speed
    const isMedia = clipTraits(c.type).timedSource
    const origDur = clipDuration(c)
    let snapped: number | null = null
    let newDur: number, newStart = c.start, newIn = c.in
    if (d.mode === 'trimR') {
      let end = c.start + origDur + dt
      if (magnet && !e.altKey) { const s = nearestPt(d.pts, end, thr); if (s != null) { end = s; snapped = s } }
      if (!d.ripple) end = Math.min(end, d.nextStart)
      if (d.mediaDur != null) end = Math.min(end, c.start + (d.mediaDur - c.in) / spd)
      newDur = Math.max(0.1, end - c.start)
    } else {
      let ns = c.start + dt
      if (magnet && !e.altKey) { const s = nearestPt(d.pts, ns, thr); if (s != null) { ns = s; snapped = s } }
      if (!d.ripple) ns = Math.max(ns, d.prevEnd)
      ns = Math.max(0, ns)
      if (isMedia) ns = Math.max(ns, c.start - c.in / spd)           // cannot go before source start
      ns = Math.min(ns, c.start + origDur - 0.1)
      const shift = ns - c.start
      newIn = isMedia ? c.in + shift * spd : 0
      newDur = origDur - shift
      newStart = d.ripple ? c.start : ns
    }
    showSnap(snapped)
    const newOut = (isMedia ? newIn : 0) + newDur * spd
    const delta = newDur - origDur
    st.mutate((proj) => ({
      ...proj,
      tracks: proj.tracks.map((t) => {
        if (t.id !== d.trackId) return t
        return {
          ...t,
          clips: t.clips.map((x) => {
            if (x.id === c.id) return { ...x, start: newStart, in: newIn, out: newOut }
            if (d.ripple) { const l = d.later.find((q) => q.id === x.id); if (l) return { ...x, start: Math.max(0, l.start + delta) } }
            return x
          }),
        }
      }),
    }), d.gid)
    void ripple
    void p
  }, [])

  const onWinUp = useCallback(() => {
    const d = drag.current
    window.removeEventListener('pointermove', onWinMove)
    window.removeEventListener('pointerup', onWinUp)
    drag.current = null
    showSnap(null)
    if (!d) return
    const st = useProject.getState()
    if (d.mode === 'move' && d.moved && d.newZone) {
      st.mutate((proj) => {
        const f = proj.tracks.find((t) => t.clips.some((c) => c.id === d.primary))
        if (!f) return proj
        const clip = f.clips.find((c) => c.id === d.primary)!
        const q = { ...proj, tracks: proj.tracks.map((t) => ({ ...t, clips: t.clips.slice() })) }
        const nt = createTrack(q, d.kind)
        q.tracks.forEach((t) => { t.clips = t.clips.filter((c) => c.id !== d.primary) })
        nt.clips.push(clip)
        return q
      }, d.gid)
    }
    if (d.mode === 'move' && d.moved) st.mutate((proj) => ({ ...proj, tracks: proj.tracks.map((t) => ({ ...t, clips: [...t.clips].sort((a, b) => a.start - b.start) })) }), d.gid)
    setNewZone(false)
  }, [onWinMove])

  useEffect(() => () => {
    window.removeEventListener('pointermove', onWinMove)
    window.removeEventListener('pointerup', onWinUp)
  }, [onWinMove, onWinUp])

  const onClipDown = useCallback((e: React.PointerEvent, clip: Clip, mode: 'move' | 'trimL' | 'trimR') => {
    if (e.button !== 0) return
    const st = useProject.getState()
    const track = st.project.tracks.find((t) => t.clips.some((c) => c.id === clip.id))
    if (!track) return
    if (mode === 'move' && (e.shiftKey || e.ctrlKey || e.metaKey)) { st.select([clip.id], true); return }
    if (!st.selection.includes(clip.id)) st.select([clip.id])
    if (track.locked) return
    e.preventDefault()
    const px0 = e.clientX
    if (mode === 'move') {
      const ids = st.selection.includes(clip.id) ? st.selection.slice() : [clip.id]
      const orig = new Map<string, { start: number; trackId: string }>()
      for (const t of st.project.tracks) for (const c of t.clips) if (ids.includes(c.id) && !t.locked) orig.set(c.id, { start: c.start, trackId: t.id })
      const okIds = ids.filter((i) => orig.has(i))
      drag.current = {
        mode: 'move', gid: newGesture(), ids: okIds, primary: clip.id, px0, py0: e.clientY, moved: false, orig,
        dur: clipDuration(clip), kind: kindForType(clip.type), pts: snapPoints(new Set(okIds)), newZone: false,
      }
    } else {
      const end = clipEnd(clip)
      const others = track.clips.filter((c) => c.id !== clip.id)
      const prevEnd = Math.max(0, ...others.filter((c) => clipEnd(c) <= clip.start + EPS).map(clipEnd))
      const nextStart = Math.min(Infinity, ...others.filter((c) => c.start >= end - EPS).map((c) => c.start))
      const media = clip.assetId && clipTraits(clip.type).timedSource ? useMedia.getState().byId[clip.assetId] : undefined
      drag.current = {
        mode, gid: newGesture(), clip, trackId: track.id, px0, pts: snapPoints(new Set([clip.id])), ripple: st.ripple,
        later: others.filter((c) => c.start >= end - EPS).map((c) => ({ id: c.id, start: c.start })), prevEnd, nextStart,
        mediaDur: media?.duration || undefined,
      }
    }
    window.addEventListener('pointermove', onWinMove)
    window.addEventListener('pointerup', onWinUp)
  }, [onWinMove, onWinUp])

  // ---- ruler scrubbing
  const scrubbing = useRef(false)
  const scrubTo = (clientX: number, alt: boolean) => {
    let t = timeAtX(clientX)
    if (live.current.magnet && !alt) {
      const pts: number[] = [0]
      for (const tr of live.current.project.tracks) for (const c of tr.clips) pts.push(c.start, clipEnd(c))
      const s = nearestPt(pts, t, 6 / live.current.zoom)
      if (s != null) t = s
    }
    player.seek(t)
  }
  const onRulerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    scrubbing.current = true
    scrubTo(e.clientX, e.altKey)
  }
  const onRulerMove = (e: React.PointerEvent) => { if (scrubbing.current) scrubTo(e.clientX, e.altKey) }
  const onRulerUp = () => { scrubbing.current = false }

  // ---- marquee (rubber-band) selection; a plain click on empty space deselects and seeks
  const startMarquee = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    const tg = e.target as HTMLElement
    if (tg.closest('[data-clip]') || tg.closest('.ve-th-wrap') || tg.closest('.ve-ruler-row')) return
    const r0 = innerRef.current!.getBoundingClientRect()
    const x0 = e.clientX - r0.left, y0 = e.clientY - r0.top
    const additive = e.shiftKey || e.ctrlKey || e.metaKey
    const base = additive ? useProject.getState().selection.slice() : []
    let moved = false, lastKey = ''
    const box = marqueeRef.current!
    const move = (ev: PointerEvent) => {
      const r = innerRef.current!.getBoundingClientRect()
      const x1 = ev.clientX - r.left, y1 = ev.clientY - r.top
      if (!moved && Math.hypot(x1 - x0, y1 - y0) < 4) return
      moved = true
      const L = Math.min(x0, x1), R = Math.max(x0, x1), T = Math.min(y0, y1), B = Math.max(y0, y1)
      box.style.display = 'block'
      box.style.left = `${L}px`; box.style.top = `${T}px`; box.style.width = `${R - L}px`; box.style.height = `${B - T}px`
      const { zoom, rows, tops } = live.current
      const tA = (L - HEADER_W) / zoom, tB = (R - HEADER_W) / zoom
      const ids = new Set(base)
      rows.forEach((row, i) => {
        const top = tops[i], bot = top + laneH(row.kind)
        if (bot < T || top > B) return
        for (const c of row.clips) if (c.start < tB && clipEnd(c) > tA) ids.add(c.id)
      })
      const key = [...ids].sort().join(',')
      if (key !== lastKey) { lastKey = key; useProject.getState().select([...ids]) }
    }
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      box.style.display = 'none'
      if (!moved) {
        if (!additive) useProject.getState().select([])
        if (x0 > HEADER_W) player.seek(timeAtX(ev.clientX))
      }
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }

  // ---- track reorder by dragging the grip (array order == z-order; only within the same kind)
  const onGripDown = useCallback((e: React.PointerEvent, trackId: string) => {
    if (e.button !== 0) return
    e.preventDefault(); e.stopPropagation()
    const gid = newGesture()
    const kind = live.current.project.tracks.find((t) => t.id === trackId)?.kind
    setDragTrack(trackId)
    const move = (ev: PointerEvent) => {
      const { row } = rowAtY(ev.clientY)
      if (!row || row.id === trackId || row.kind !== kind) return
      useProject.getState().mutate((p) => {
        const from = p.tracks.findIndex((t) => t.id === trackId), to = p.tracks.findIndex((t) => t.id === row.id)
        if (from < 0 || to < 0 || from === to) return p
        const tracks = p.tracks.slice()
        const [m] = tracks.splice(from, 1)
        tracks.splice(to, 0, m)
        return { ...p, tracks }
      }, gid)
    }
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); setDragTrack(null) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- drops (library assets / OS files)
  const onDragOver = (e: React.DragEvent) => {
    if (Array.from(e.dataTransfer.types).includes(DND_MIME) || hasFiles(e.dataTransfer)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setDropHint(true) }
  }
  const onDrop = async (e: React.DragEvent) => {
    setDropHint(false)
    const st = useProject.getState()
    const start = timeAtX(e.clientX)
    const { row, below } = rowAtY(e.clientY)
    const raw = e.dataTransfer.getData(DND_MIME)
    const withTrack = (kind: TrackKind): string | undefined => {
      if (below) return st.addTrack(kind)
      return row && row.kind === kind && !row.locked ? row.id : undefined
    }
    if (raw) {
      e.preventDefault()
      const pl = JSON.parse(raw) as DndPayload
      if (pl.type === 'media' && pl.id) {
        const m = useMedia.getState().byId[pl.id]
        if (m) st.addMedia(m, { start, track: withTrack(kindForType(m.kind)) })
      } else if (pl.type === 'sticker' && pl.id) st.addSticker(pl.id, pl.name || 'sticker', start)
      else if (pl.type === 'text') st.addTextPreset((pl.style || {}) as any, pl.name, start)
      return
    }
    if (hasFiles(e.dataTransfer)) {
      e.preventDefault()
      const files = Array.from(e.dataTransfer.files)
      const rowsUp = await uploadFiles(files, (m) => st.say(m))
      let t = start
      for (const m of rowsUp) {
        useProject.getState().addMedia(m, { start: t, track: withTrack(kindForType(m.kind)) })
        t += m.kind === 'image' ? 5 : m.duration || 5
      }
    }
  }

  const selSet = useMemo(() => new Set(selection), [selection])
  const hasSel = selection.length > 0
  const canDetach = useMemo(() => project.tracks.some((t) => t.clips.some((c) => selSet.has(c.id) && c.type === 'video' && !c.audioDetached && byId[c.assetId || '']?.has_audio !== false)), [project, selSet, byId])
  const [menu, setMenu] = useState<{ clip: Clip; x: number; y: number } | null>(null)
  const closeMenu = useCallback(() => setMenu(null), [])
  const onContextMenu = (e: React.MouseEvent) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('[data-clip]')
    if (!el) return
    const id = el.dataset.clip
    const st = useProject.getState()
    const clip = st.project.tracks.flatMap((t) => t.clips).find((c) => c.id === id)
    if (!clip) return
    e.preventDefault()
    if (!st.selection.includes(clip.id)) st.select([clip.id])
    setMenu({ clip, x: e.clientX, y: e.clientY })
  }
  const fitZoom = () => {
    const w = (scrollRef.current?.clientWidth ?? 800) - HEADER_W - 40
    useProject.getState().setZoom(Math.max(4, w / Math.max(dur, 5)))
    if (scrollRef.current) scrollRef.current.scrollLeft = 0
  }
  const st = useProject.getState

  return (
    <div className="ve-timeline">
      <div className="ve-tl-bar">
        <IconBtn title="Split (S)" onClick={() => st().splitAtPlayhead()}><Scissors size={15} /></IconBtn>
        <IconBtn title="Delete (Del)" disabled={!hasSel} onClick={() => st().deleteSelected(false)}><Trash2 size={15} /></IconBtn>
        <IconBtn title="Ripple delete (Shift+Del)" disabled={!hasSel} onClick={() => st().deleteSelected(true)}><ChevronsRight size={15} /></IconBtn>
        <IconBtn title="Duplicate (Ctrl+D)" disabled={!hasSel} onClick={() => st().duplicateSelected()}><Copy size={15} /></IconBtn>
        <IconBtn title="Split audio onto its own track (Ctrl+Shift+S)" disabled={!canDetach} onClick={() => st().detachAudioSelected()}><AudioLines size={15} /></IconBtn>
        <span className="ve-sep" />
        <IconBtn title="Snapping" active={magnet} onClick={() => useProject.setState({ magnet: !magnet })}><Magnet size={15} /></IconBtn>
        <button className={`ve-chipbtn ${ripple ? 'on' : ''}`} title="When trimming, shift the following clips (ripple)" onClick={() => useProject.setState({ ripple: !ripple })}>Ripple trim</button>
        <span className="ve-sep" />
        <button className="ve-chipbtn" onClick={() => st().addTrack('video')}><Plus size={11} />Video track</button>
        <button className="ve-chipbtn" onClick={() => st().addTrack('audio')}><Plus size={11} />Audio track</button>
        <button className="ve-chipbtn" onClick={() => st().addTrack('text')}><Plus size={11} />Text track</button>
        <div className="grow" />
        <IconBtn title="Zoom out (-)" onClick={() => st().setZoom(zoom / 1.3)}><Minus size={14} /></IconBtn>
        <input className="ve-range ve-tl-zoom" type="range" min={Math.log(4)} max={Math.log(600)} step={0.01} value={Math.log(zoom)}
          onChange={(e) => st().setZoom(Math.exp(parseFloat(e.target.value)))} />
        <IconBtn title="Zoom in (+)" onClick={() => st().setZoom(zoom * 1.3)}><Plus size={14} /></IconBtn>
        <IconBtn title="Fit to window" onClick={fitZoom}><ZoomIn size={14} /></IconBtn>
      </div>
      <div className="ve-tl-scroll" ref={scrollRef}
        onDragOver={onDragOver} onDragLeave={() => setDropHint(false)} onDrop={onDrop}>
        <div className={`ve-tl-inner ${dropHint ? 'drop' : ''}`} ref={innerRef} onPointerDown={startMarquee} onContextMenu={onContextMenu} style={{ width: HEADER_W + contentW, height: innerH, minHeight: '100%' }}>
          <div className="ve-ruler-row" style={{ height: RULER_H }}>
            <div className="ve-corner" style={{ width: HEADER_W }}>
              <span className="faint" style={{ fontSize: 11 }}>{project.width}×{project.height}</span>
            </div>
            <div className="ve-ruler" style={{ width: contentW }} onPointerDown={onRulerDown} onPointerMove={onRulerMove} onPointerUp={onRulerUp}>
              <Ruler zoom={zoom} width={contentW} fps={project.fps} />
            </div>
          </div>
          {rows.map((tr, i) => (
            <div key={tr.id} className={`ve-lane ${tr.hidden ? 'hidden' : ''} ${tr.locked ? 'locked' : ''} ${dragTrack === tr.id ? 'dragging' : ''}`} style={{ height: laneH(tr.kind), top: tops[i] }}>
              <div className="ve-th-wrap" style={{ width: HEADER_W }}><TrackHeader track={tr} height={laneH(tr.kind)} onGrip={onGripDown} dragging={dragTrack === tr.id} /></div>
              <div className={`ve-lane-body k-${tr.kind}`} style={{ width: contentW }}>
                {tr.clips.map((c) => (
                  <ClipView key={c.id} clip={c} zoom={zoom} selected={selSet.has(c.id)} locked={tr.locked} trackKind={tr.kind}
                    height={laneH(tr.kind)} media={c.assetId ? byId[c.assetId] : undefined} onDown={onClipDown} />
                ))}
              </div>
            </div>
          ))}
          <div className={`ve-newzone ${newZone ? 'on' : ''}`} style={{ top: lanesBottom, height: NEW_ZONE_H, left: HEADER_W }}>
            {newZone ? 'Release to create a new track' : rows.length === 0 ? 'Drop media here to start editing' : 'Drag media here to create a new track'}
          </div>
          {rows.length > 0 && !dropHint && rows.every((r) => !r.clips.length) && (
            <div className="ve-tl-empty" style={{ top: RULER_H, height: lanesBottom - RULER_H, left: HEADER_W + 24, right: 'auto' }}>
              <span>Drag clips from the Media panel onto a track, or double-click an item to append it</span>
            </div>
          )}
          <div className="ve-snap" ref={snapRef} />
          <div className="ve-marquee" ref={marqueeRef} />
          <div className="ve-playhead" ref={phRef}><i /></div>
        </div>
      </div>
      {menu && <ClipMenu clip={menu.clip} x={menu.x} y={menu.y} onClose={closeMenu} />}
    </div>
  )
}

/** Place clips at new (start, track) targets; keeps identity of untouched clips. */
function placeClips(p: Project, targets: Map<string, { start: number; trackId: string }>): Project {
  let changed = false
  const moving: Clip[] = []
  const tracks = p.tracks.map((t) => {
    if (!t.clips.some((c) => targets.has(c.id))) return t
    changed = true
    return { ...t, clips: t.clips.filter((c) => { if (targets.has(c.id)) { moving.push(c); return false } return true }) }
  })
  if (!changed) return p
  const out = tracks.map((t) => {
    const add = moving.filter((c) => targets.get(c.id)!.trackId === t.id)
    if (!add.length) return t
    return { ...t, clips: [...t.clips, ...add.map((c) => ({ ...c, start: targets.get(c.id)!.start }))] }
  })
  return { ...p, tracks: out }
}
