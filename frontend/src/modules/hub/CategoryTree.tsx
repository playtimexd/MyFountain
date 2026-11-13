// Library category tree for the Asset Hub sidebar: folders with live counts (respecting the other filters).
import { useEffect, useState } from 'react'
import { AudioLines, Box, ChevronDown, ChevronRight, Film, Image as ImageIcon, Layers, Library, PersonStanding, Shapes, Type, Folder } from 'lucide-react'
import type { CategoryNode } from './api'

const ROOT_ICONS: Record<string, typeof Folder> = {
  video: Film, image: ImageIcon, audio: AudioLines, models: Box, anim: PersonStanding, surfaces: Layers, icons: Shapes, fonts: Type,
}
const OPEN_KEY = 'cutlab.hub.catOpen'

function loadOpen(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(OPEN_KEY) || '[]')) } catch { return new Set() }
}

export default function CategoryTree({ tree, counts, value, onChange, total }: {
  tree: CategoryNode[]; counts: Record<string, number>; value: string; onChange: (id: string) => void; total: number
}) {
  const [open, setOpen] = useState<Set<string>>(loadOpen)
  // keep the selected folder's ancestors expanded
  useEffect(() => {
    if (!value) return
    const parts = value.split('/')
    setOpen((o) => {
      const n = new Set(o); let changed = false
      for (let i = 1; i < parts.length; i++) { const k = parts.slice(0, i).join('/'); if (!n.has(k)) { n.add(k); changed = true } }
      return changed ? n : o
    })
  }, [value])
  useEffect(() => { try { localStorage.setItem(OPEN_KEY, JSON.stringify([...open])) } catch { /* ignore */ } }, [open])
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n })

  const row = (n: CategoryNode, depth: number): React.ReactNode => {
    const c = counts[n.id] ?? 0
    const kids = n.children.filter((k) => (counts[k.id] ?? 0) > 0 || value === k.id || value.startsWith(k.id + '/'))
    if (!c && value !== n.id && !value.startsWith(n.id + '/')) return null
    const Icon = depth === 0 ? (ROOT_ICONS[n.id] ?? Folder) : null
    const isOpen = open.has(n.id)
    return (
      <div key={n.id}>
        <div className={`facet cat-row${value === n.id ? ' on' : ''}`} style={{ paddingLeft: 4 + depth * 12 }}
          onClick={() => { onChange(value === n.id ? '' : n.id); if (kids.length && !isOpen) toggle(n.id) }} title={n.id}>
          <span className="cat-twisty" onClick={(e) => { e.stopPropagation(); if (kids.length) toggle(n.id) }}>
            {kids.length ? (isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />) : null}
          </span>
          {Icon && <Icon size={13} className="cat-icon" />}
          <span className="cat-label">{n.label}</span><span className="n">{c.toLocaleString()}</span>
        </div>
        {kids.length > 0 && isOpen && kids.map((k) => row(k, depth + 1))}
      </div>
    )
  }

  return (
    <div className="cat-tree">
      <div className={`facet cat-row${!value ? ' on' : ''}`} style={{ paddingLeft: 4 }} onClick={() => onChange('')}>
        <span className="cat-twisty" /><Library size={13} className="cat-icon" /><span className="cat-label">All assets</span><span className="n">{total.toLocaleString()}</span>
      </div>
      {tree.map((n) => row(n, 0))}
    </div>
  )
}
