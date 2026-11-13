import { useEffect, useRef, useState } from 'react'
import './video.css'
import { on } from '../../shared/bus'
import { useProject } from './store'
import { useCatalog, useMedia } from './data'
import { player } from './engine/player'
import { clock } from './engine/clock'
import { routes } from './engine/mediaPool'
import { startBridge } from './bridge'
import TopBar from './TopBar'
import VideoProjects, { videoSession } from './VideoProjects'
import LeftPanel from './panels/LeftPanel'
import Preview from './preview/Preview'
import Inspector from './inspector/Inspector'
import Timeline from './timeline/Timeline'
import { smartStart } from './ops'
import type { Op } from '../../types/project'
import { allClipsInOrder } from './keys'

if (import.meta.env.DEV) (window as any).__ve = { useProject, player, clock, routes }

export default function VideoEditor() {
  const rootRef = useRef<HTMLDivElement>(null)
  const toast = useProject((s) => s.toast)
  const [tlH, setTlH] = useState(() => {
    const v = parseInt(localStorage.getItem('cutlab.video.tlh') || '', 10)
    return Number.isFinite(v) ? v : 300
  })

  useEffect(() => { void useCatalog.getState().load(); void useMedia.getState().refresh() }, [])

  // ---- cross-module events
  useEffect(() => {
    const offs = [
      on('cutlab:add-to-timeline', ({ media, in: i, out: o }) => {
        useMedia.getState().upsert(media)
        useProject.getState().addMedia(media, { in: i, out: o })
      }),
      on('cutlab:add-segments', ({ media, segments }) => {
        useMedia.getState().upsert(media)
        const st = useProject.getState()
        let t = smartStart(st.project, media.kind === 'audio' ? 'audio' : 'video', clock.time, Math.max(0.1, segments[0] ? segments[0].end - segments[0].start : 1))
        const ops: Op[] = []
        for (const s of segments) {
          ops.push({ op: 'add_clip', assetId: media.id, start: t, in: s.start, out: s.end })
          t += s.end - s.start
        }
        void st.applyOps(ops)
      }),
      on('cutlab:media-changed', () => { void useMedia.getState().refresh() }),
    ]
    return () => offs.forEach((f) => f())
  }, [])

  // pause when the tab is switched away (App hides inactive modules with display:none)
  useEffect(() => {
    const root = rootRef.current!
    const ro = new ResizeObserver(() => { if (root.offsetParent === null) player.pause() })
    ro.observe(root)
    return () => ro.disconnect()
  }, [])

  // ---- live bridge (MCP / external drivers)
  useEffect(() => startBridge(() => { const r = rootRef.current; return !!r && r.offsetParent !== null }), [])

  // ---- keyboard
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const root = rootRef.current
      if (!root || root.offsetParent === null) return // tab hidden
      if (document.querySelector('.modal-back') || videoSession.use.getState().home) return // dialogs / Projects screen
      const el = e.target as HTMLElement
      const tag = el.tagName
      const typing = tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable || (tag === 'INPUT' && !['range', 'checkbox', 'color', 'button'].includes((el as HTMLInputElement).type))
      if (typing) return
      const st = useProject.getState()
      const ctrl = e.ctrlKey || e.metaKey
      const key = e.key
      const fps = st.project.fps
      let handled = true
      if (ctrl && key.toLowerCase() === 'z') { e.shiftKey ? st.redo() : st.undo() }
      else if (ctrl && key.toLowerCase() === 'y') st.redo()
      else if (ctrl && key.toLowerCase() === 'd') st.duplicateSelected()
      else if (ctrl && e.shiftKey && key.toLowerCase() === 's') st.detachAudioSelected()
      else if (ctrl && key.toLowerCase() === 'a') st.select(allClipsInOrder(st.project).map((c) => c.id))
      else if (ctrl) handled = false
      else if (key === ' ') player.toggle()
      else if (key.toLowerCase() === 's') st.splitAtPlayhead()
      else if (key === 'Delete' || key === 'Backspace') st.deleteSelected(e.shiftKey)
      else if (key === 'ArrowLeft' || key === 'ArrowRight') {
        if (tag === 'INPUT') { handled = false }
        else { player.pause(); player.seek(clock.time + ((key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 10 : 1)) / fps) }
      }
      else if (key === 'Home') { player.seek(0) }
      else if (key === 'End') { player.seek(player.duration) }
      else if (key === '+' || key === '=') st.setZoom(st.zoom * 1.25)
      else if (key === '-' || key === '_') st.setZoom(st.zoom / 1.25)
      else if (key === 'Escape') st.select([])
      else handled = false
      if (handled) e.preventDefault()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  // ---- timeline splitter
  const startResize = (e: React.PointerEvent) => {
    const y0 = e.clientY, h0 = tlH
    const move = (ev: PointerEvent) => setTlH(Math.min(window.innerHeight - 260, Math.max(160, h0 - (ev.clientY - y0))))
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      setTlH((h) => { try { localStorage.setItem('cutlab.video.tlh', String(h)) } catch { /* ignore */ } return h })
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }

  return (
    <div className="ve" ref={rootRef} style={{ gridTemplateRows: `42px minmax(0,1fr) 6px ${tlH}px` }}>
      <TopBar />
      <div className="ve-main">
        <LeftPanel />
        <Preview />
        <Inspector />
      </div>
      <div className="ve-splitter" onPointerDown={startResize} />
      <Timeline />
      {toast && <div className="ve-toast">{toast}</div>}
      <VideoProjects />
    </div>
  )
}
