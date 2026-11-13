// On-canvas text editing: a transparent contenteditable positioned exactly over the text layer (caret + selection only;
// the real glyphs keep being rendered by the layer so strokes/shadows/effects stay visible while typing).
import { useEffect, useRef } from 'react'
import { useUI, curDoc } from './store'
import { textMetrics } from './render'
import { afterDraw, toScreen } from './view'
import type { Doc } from './doc'

let session: { doc: Doc; id: string } | null = null

export function startTextEdit(id: string) {
  const d = curDoc()
  const L = d?.layer(id)
  if (!d || !L || L.type !== 'text' || L.locked) return
  if (session) stopTextEdit()
  d.activeId = id
  d.history.begin('Edit text')
  session = { doc: d, id }
  useUI.setState((s) => ({ editingText: id, rev: s.rev + 1 }))
}

export function stopTextEdit() {
  if (!session) return
  const { doc, id } = session
  session = null
  const L = doc.layer(id)
  doc.history.end()
  useUI.setState((s) => ({ editingText: null, rev: s.rev + 1 }))
  if (L && L.type === 'text' && !(L.text?.text ?? '').trim()) doc.removeLayer(L.id, 'Delete empty text')
  doc.changed()
}

export default function TextEditor() {
  const id = useUI((s) => s.editingText)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    const d = curDoc()
    const L = id ? d?.layer(id) : undefined
    if (!el || !d || !L || !L.text) return
    el.textContent = L.text.text
    el.focus()
    const sel = window.getSelection()
    if (sel) { const r = document.createRange(); r.selectNodeContents(el); sel.removeAllRanges(); sel.addRange(r) }
    const stop = (e: Event) => e.stopPropagation()
    el.addEventListener('pointerdown', stop); el.addEventListener('pointerup', stop); el.addEventListener('pointermove', stop); el.addEventListener('dblclick', stop)
    const sync = () => {
      const doc = curDoc()
      const layer = doc?.layer(L.id)
      if (!doc || !layer?.text || !el.isConnected) return
      const t = layer.text
      const m = textMetrics(t)
      const cw = layer.canvas.width, ch = layer.canvas.height
      const lx = (m.pad - cw / 2) * layer.t.sx, ly = (m.pad - ch / 2) * layer.t.sy
      const c = Math.cos(layer.t.rot), s = Math.sin(layer.t.rot)
      const [sx, sy] = toScreen(doc, layer.t.cx + lx * c - ly * s, layer.t.cy + lx * s + ly * c)
      const z = doc.view.zoom
      const st = el.style
      st.left = `${sx}px`; st.top = `${sy}px`
      st.width = `${m.tw}px`; st.height = `${m.th}px`
      st.transform = `rotate(${layer.t.rot}rad) scale(${z * layer.t.sx}, ${z * layer.t.sy})`
      st.font = `${t.italic ? 'italic ' : ''}${t.bold ? 'bold ' : ''}${t.size}px "${t.font}", "Microsoft YaHei", sans-serif`
      st.lineHeight = `${t.size * t.lineHeight}px`
      st.letterSpacing = `${t.letterSpacing}px`
      st.textAlign = t.align
      if (document.activeElement !== el && el.innerText.replace(/\n$/, '') !== t.text) el.textContent = t.text
    }
    afterDraw.add(sync)
    sync()
    return () => { afterDraw.delete(sync); el.removeEventListener('pointerdown', stop); el.removeEventListener('pointerup', stop); el.removeEventListener('pointermove', stop); el.removeEventListener('dblclick', stop) }
  }, [id])

  if (!id) return null
  const commitText = () => {
    const d = curDoc(), L = d?.layer(id)
    const el = ref.current
    if (!d || !L || !el) return
    const v = el.innerText.replace(/\n$/, '')
    if (v === L.text?.text) return
    d.mutate(L, 'Edit text', (l) => { l.text!.text = v; l.name = (v.split('\n')[0] || 'Text').slice(0, 16) }, 'text-content')
  }
  return (
    <div
      ref={ref}
      className="ie-textedit"
      contentEditable="plaintext-only"
      suppressContentEditableWarning
      spellCheck={false}
      onInput={commitText}
      onBlur={() => stopTextEdit()}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); stopTextEdit() }
      }}
    />
  )
}
