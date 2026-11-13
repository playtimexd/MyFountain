// Floating "Color Range" panel with live marching-ants preview (the canvas stays clickable to re-sample the colour).
import { useEffect, useRef } from 'react'
import { Check, ColorChip, Slider } from './controls'
import { computeColorRange } from './rangemask'
import { hooks } from './doc'
import { selFromMask } from './selection'
import { curDoc, useUI } from './store'
import { getSampleData } from './tools'
import type { Sel } from './types'
import { parseColor, toHex } from './util'

export interface ColorRangeState { color: string; fuzz: number; all: boolean; invert: boolean }
let backup: Sel | null = null

export function openColorRange() {
  const d = curDoc()
  if (!d) return
  backup = d.sel
  useUI.setState({ colorRange: { color: useUI.getState().fg, fuzz: 40, all: true, invert: false } })
}
export function closeColorRange(apply: boolean) {
  const d = curDoc()
  const st = useUI.getState()
  if (!d || !st.colorRange) { useUI.setState({ colorRange: null }); return }
  const preview = d.sel
  d.sel = backup
  if (apply) d.setSel(preview, 'Color Range')
  useUI.setState({ colorRange: null })
  hooks.render(); hooks.ui()
}

export default function ColorRangePanel() {
  const cr = useUI((s) => s.colorRange)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => {
    if (!cr) return
    clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const d = curDoc()
      if (!d) return
      const img = getSampleData(d, d.active, cr.all)
      d.sel = selFromMask(d.w, d.h, computeColorRange(img, parseColor(cr.color), cr.fuzz, cr.invert))
      hooks.render()
    }, 40)
    return () => clearTimeout(timer.current)
  }, [cr])
  if (!cr) return null
  const set = (p: Partial<ColorRangeState>) => useUI.setState({ colorRange: { ...cr, ...p } })
  return (
    <div className="ie-floatcard" role="dialog">
      <div className="mh"><b className="grow">Color Range</b></div>
      <div className="mb">
        <div className="row" style={{ marginBottom: 8 }}>
          <ColorChip value={cr.color} onChange={(c) => set({ color: toHex(parseColor(c), false) })} alpha={false} label="Sampled colour" />
        </div>
        <div className="faint" style={{ fontSize: 11.5, marginBottom: 8 }}>Click on the image to sample a colour. The selection updates live.</div>
        <Slider label="Fuzziness" value={cr.fuzz} min={0} max={100} onChange={(v) => set({ fuzz: v })} />
        <div className="row wrap" style={{ marginTop: 6 }}>
          <Check label="All layers" checked={cr.all} onChange={(v) => set({ all: v })} />
          <Check label="Invert" checked={cr.invert} onChange={(v) => set({ invert: v })} />
        </div>
      </div>
      <div className="mf">
        <button className="btn" onClick={() => closeColorRange(false)}>Cancel</button>
        <button className="btn primary" onClick={() => closeColorRange(true)}>OK</button>
      </div>
    </div>
  )
}
