import { useEffect, useRef } from 'react'
import { AudioLines, Copy, Eraser, Film, Scissors, Subtitles, Trash2, ChevronsRight, Wand2 } from 'lucide-react'
import type { Clip } from '../../../types/project'
import { useProject } from '../store'
import { AI_TOOLS, useAiFocus } from '../../ai/focus'
import type { AiToolId } from '../../ai/focus'

const AI_ICON: Record<AiToolId, typeof Film> = { upscale: Film, watermark: Eraser, removebg: Wand2, subtitles: Subtitles, smartcut: Scissors }

/** Right-click menu for a timeline clip. */
export default function ClipMenu({ clip, x, y, onClose }: { clip: Clip; x: number; y: number; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const down = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose() }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('mousedown', down, true)
    window.addEventListener('keydown', key, true)
    window.addEventListener('blur', onClose)
    return () => { window.removeEventListener('mousedown', down, true); window.removeEventListener('keydown', key, true); window.removeEventListener('blur', onClose) }
  }, [onClose])
  // keep the menu inside the window
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    if (r.right > window.innerWidth - 4) el.style.left = `${Math.max(4, window.innerWidth - r.width - 4)}px`
    if (r.bottom > window.innerHeight - 4) el.style.top = `${Math.max(4, window.innerHeight - r.height - 4)}px`
  }, [])

  const st = useProject.getState
  const kind = clip.type === 'sticker' ? 'image' : clip.type
  const tools = clip.assetId ? AI_TOOLS.filter((t) => (t.kinds as string[]).includes(kind)) : []
  const act = (fn: () => void) => () => { onClose(); fn() }

  return (
    <div className="ctx-menu" ref={ref} style={{ left: x, top: y }} onContextMenu={(e) => e.preventDefault()}>
      {tools.length > 0 && <>
        <div className="ctx-label">AI tools</div>
        {tools.map((t) => {
          const I = AI_ICON[t.id]
          return <button key={t.id} className="ctx-item" onClick={act(() => useAiFocus.getState().open(t.id, clip.assetId || null))}><I size={14} />{t.label}…</button>
        })}
        <div className="ctx-sep" />
      </>}
      <button className="ctx-item" onClick={act(() => st().splitAtPlayhead())}><Scissors size={14} />Split at playhead<span className="k">S</span></button>
      <button className="ctx-item" onClick={act(() => st().duplicateSelected())}><Copy size={14} />Duplicate<span className="k">Ctrl+D</span></button>
      {clip.type === 'video' && !clip.audioDetached && (
        <button className="ctx-item" onClick={act(() => st().detachAudioSelected())}><AudioLines size={14} />Split audio<span className="k">Ctrl+Shift+S</span></button>
      )}
      <div className="ctx-sep" />
      <button className="ctx-item danger" onClick={act(() => st().deleteSelected(false))}><Trash2 size={14} />Delete<span className="k">Del</span></button>
      <button className="ctx-item danger" onClick={act(() => st().deleteSelected(true))}><ChevronsRight size={14} />Ripple delete<span className="k">Shift+Del</span></button>
    </div>
  )
}
