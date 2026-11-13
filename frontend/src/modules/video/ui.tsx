import { useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

export function Slider({ label, value, min, max, step = 0.01, def, onChange, unit, digits = 2, k }: {
  label: string; value: number; min: number; max: number; step?: number; def?: number
  onChange: (v: number, key: string) => void; unit?: string; digits?: number; k?: string
}) {
  const key = `sl:${k ?? label}`
  const shown = Number.isFinite(value) ? +value.toFixed(digits) : 0
  return (
    <div className="ve-slider">
      <div className="ve-slider-head">
        <span className="ve-slider-label" title="Double-click to reset" onDoubleClick={() => def != null && onChange(def, key)}>{label}</span>
        <input
          className="ve-num" type="number" value={shown} step={step} min={min} max={max}
          onChange={(e) => { const v = parseFloat(e.target.value); if (Number.isFinite(v)) onChange(v, key) }}
        />
        {unit && <span className="faint" style={{ fontSize: 11 }}>{unit}</span>}
      </div>
      <input
        className="ve-range" type="range" min={min} max={max} step={step} value={Math.min(max, Math.max(min, value))}
        onChange={(e) => onChange(parseFloat(e.target.value), key)}
        style={{ ['--p' as any]: `${((Math.min(max, Math.max(min, value)) - min) / (max - min)) * 100}%` }}
      />
    </div>
  )
}

export function Section({ title, children, defaultOpen = true, right }: { title: string; children: ReactNode; defaultOpen?: boolean; right?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="ve-section">
      <div className="ve-section-head" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="grow">{title}</span>
        <span onClick={(e) => e.stopPropagation()}>{right}</span>
      </div>
      {open && <div className="ve-section-body">{children}</div>}
    </div>
  )
}

export function Toggle({ on, onClick, children, title }: { on: boolean; onClick: () => void; children: ReactNode; title?: string }) {
  return <button className={`ve-toggle ${on ? 'on' : ''}`} onClick={onClick} title={title}>{children}</button>
}

export function IconBtn({ children, onClick, title, disabled, active, danger }: { children: ReactNode; onClick?: () => void; title?: string; disabled?: boolean; active?: boolean; danger?: boolean }) {
  return (
    <button className={`ve-ibtn ${active ? 'on' : ''} ${danger ? 'danger' : ''}`} onClick={onClick} title={title} disabled={disabled}>{children}</button>
  )
}

export const fmtTC = (s: number, fps = 30) => {
  if (!isFinite(s) || s < 0) s = 0
  const m = Math.floor(s / 60), sec = Math.floor(s % 60), f = Math.floor((s % 1) * fps)
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}:${String(f).padStart(2, '0')}`
}
