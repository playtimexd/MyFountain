import { useState } from 'react'
import { Ban } from 'lucide-react'
import { useCatalog, TRANSITION_LABEL } from '../data'
import { selectedClips, useProject } from '../store'
import { DND_MIME } from '../actions'
import type { DndPayload } from '../actions'
import { DEFAULT_ADJUST } from '../../../types/project'
import type { TransitionIn } from '../../../types/project'
import { adjustFilter } from '../engine/compositor'
import { thumbUrl } from '../../../api/client'
import { useMedia } from '../data'

export function StickerPanel() {
  const stickers = useCatalog((s) => s.catalog.stickers)
  const addSticker = useProject((s) => s.addSticker)
  if (!stickers.length) return <div className="ve-panel-body faint" style={{ textAlign: 'center', padding: 28 }}>No stickers available<br />(the engine sticker catalog is empty)</div>
  return (
    <div className="ve-panel-body">
      <div className="ve-grid4">
        {stickers.map((s) => (
          <div key={s.id} className="ve-sticker" title={s.name} draggable
            onDragStart={(e) => { const pl: DndPayload = { type: 'sticker', id: s.id, name: s.name }; e.dataTransfer.setData(DND_MIME, JSON.stringify(pl)) }}
            onClick={() => addSticker(s.id, s.name)}>
            <img src={s.url} alt={s.name} loading="lazy" draggable={false} />
          </div>
        ))}
      </div>
    </div>
  )
}

const TR_ANIM: Record<string, string> = {
  fade: 've-a-fade', slideleft: 've-a-sl', slideright: 've-a-sr', slideup: 've-a-su', slidedown: 've-a-sd',
  zoomin: 've-a-zi', wipeleft: 've-a-wl', wiperight: 've-a-wr',
}

export function TransitionPanel() {
  const transitions = useCatalog((s) => s.catalog.transitions)
  const project = useProject((s) => s.project)
  const selection = useProject((s) => s.selection)
  const setTransition = useProject((s) => s.setTransition)
  const sel = selectedClips({ project, selection })
  const cur = sel[0]?.transitionIn
  const [dur, setDur] = useState(0.6)
  const apply = (type: TransitionIn['type']) => {
    if (!sel.length) { useProject.getState().say('Select a clip on the timeline first'); return }
    setTransition(selection, { type, duration: dur })
  }
  return (
    <div className="ve-panel-body">
      <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>Select clips on the timeline, then click a transition to apply it to the start of the clip.</div>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="muted">Duration</span>
        <input type="range" className="ve-range grow" min={0.1} max={2} step={0.05} value={dur} onChange={(e) => setDur(+e.target.value)}
          style={{ ['--p' as any]: `${((dur - 0.1) / 1.9) * 100}%` }} />
        <span className="muted" style={{ width: 36 }}>{dur.toFixed(2)}s</span>
      </div>
      <div className="ve-grid4">
        <div className={`ve-fx ${!cur && sel.length ? 'on' : ''}`} onClick={() => sel.length && setTransition(selection, undefined)}>
          <div className="ve-fx-box"><Ban size={18} /></div><span>None</span>
        </div>
        {transitions.map((t) => (
          <div key={t.id} className={`ve-fx ${cur?.type === t.id ? 'on' : ''}`} onClick={() => apply(t.id as TransitionIn['type'])}>
            <div className="ve-fx-box"><i className={TR_ANIM[t.id] || 've-a-fade'} /></div>
            <span>{TRANSITION_LABEL[t.id] || t.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function FilterPanel() {
  const filters = useCatalog((s) => s.catalog.filters)
  const project = useProject((s) => s.project)
  const selection = useProject((s) => s.selection)
  const items = useMedia((s) => s.items)
  const sel = selectedClips({ project, selection })
  const sampleId = sel.find((c) => c.assetId && (c.type === 'video' || c.type === 'image'))?.assetId || items.find((m) => m.kind !== 'audio')?.id
  const apply = (id: string | null) => {
    if (!sel.length) { useProject.getState().say('Select a clip on the timeline first'); return }
    const f = filters.find((x) => x.id === id)
    useProject.getState().patchClips(selection, (c) => ({
      filter: f?.id,
      adjust: f ? { ...DEFAULT_ADJUST, ...f.adjust } : { ...DEFAULT_ADJUST, ...pickKeep(c.adjust) },
    }))
  }
  return (
    <div className="ve-panel-body">
      <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>Select clips, then click a preset. Fine-tune the values under "Adjust" in the inspector.</div>
      <div className="ve-grid3">
        <div className={`ve-fx ${sel[0] && !sel[0].filter ? 'on' : ''}`} onClick={() => apply(null)}>
          <div className="ve-fx-box"><Ban size={18} /></div><span>None</span>
        </div>
        {filters.map((f) => (
          <div key={f.id} className={`ve-fx ${sel[0]?.filter === f.id ? 'on' : ''}`} onClick={() => apply(f.id)}>
            <div className="ve-fx-box" style={{ background: '#222' }}>
              {sampleId && <img src={thumbUrl(sampleId)} alt="" draggable={false} style={{ filter: adjustFilter({ ...DEFAULT_ADJUST, ...f.adjust }, 150) }} />}
            </div>
            <span>{f.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
// when clearing a filter, reset adjust fully (the preset's values were merged in)
function pickKeep(_a: unknown) { return {} }
