import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useUI } from './store'
import { clamp, hsvToRgb, parseColor, rgbToHsv, toHex } from './util'

export function Slider({ label, value, min, max, step = 1, onChange, unit = '', width }: {
  label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void; unit?: string; width?: number
}) {
  return (
    <label className="ie-slider" style={width ? { width } : undefined}>
      <span className="lbl">{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <input className="num" type="number" min={min} max={max} step={step} value={Number.isFinite(value) ? Math.round(value * 100) / 100 : 0}
        onChange={(e) => { const v = Number(e.target.value); if (Number.isFinite(v)) onChange(clamp(v, min, max)) }} />
      {unit && <span className="unit">{unit}</span>}
    </label>
  )
}

export function Check({ label, checked, onChange, title }: { label: string; checked: boolean; onChange: (v: boolean) => void; title?: string }) {
  return (
    <label className="ie-check" title={title}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{label}
    </label>
  )
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, ReactNode, string?][]; onChange: (v: T) => void }) {
  return (
    <div className="ie-seg">
      {options.map(([v, l, t]) => <button key={v} title={t} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>{l}</button>)}
    </div>
  )
}

export function ColorPicker({ value, onChange, alpha = true }: { value: string; onChange: (c: string) => void; alpha?: boolean }) {
  const c = parseColor(value)
  const [hsv, setHsv] = useState<[number, number, number]>(() => rgbToHsv(c.r, c.g, c.b))
  const [hex, setHex] = useState(toHex(c, false))
  const last = useRef(value)
  useEffect(() => {
    if (value !== last.current) {
      const p = parseColor(value)
      const h = rgbToHsv(p.r, p.g, p.b)
      setHsv((o) => [h[1] === 0 || h[2] === 0 ? o[0] : h[0], h[1], h[2]])
      setHex(toHex(p, false)); last.current = value
    }
  }, [value])
  const emit = (h: [number, number, number], a = c.a) => {
    const [r, g, b] = hsvToRgb(h[0], h[1], h[2])
    const out = toHex({ r, g, b, a })
    last.current = out; setHsv(h); setHex(toHex({ r, g, b, a }, false)); onChange(out)
  }
  const drag = (el: HTMLElement, fn: (x: number, y: number) => void) => (e: React.PointerEvent) => {
    el.setPointerCapture(e.pointerId)
    const go = (ev: PointerEvent | React.PointerEvent) => { const r = el.getBoundingClientRect(); fn(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1)) }
    go(e)
    const mv = (ev: PointerEvent) => go(ev)
    const up = () => { el.removeEventListener('pointermove', mv); el.removeEventListener('pointerup', up) }
    el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up)
  }
  const [h, s, v] = hsv
  return (
    <div className="ie-picker">
      <div className="sv" style={{ background: `linear-gradient(to top,#000,transparent),linear-gradient(to right,#fff,hsl(${h},100%,50%))` }}
        onPointerDown={(e) => drag(e.currentTarget, (x, y) => emit([h, x, 1 - y]))(e)}>
        <i style={{ left: `${s * 100}%`, top: `${(1 - v) * 100}%` }} />
      </div>
      <div className="hue" onPointerDown={(e) => drag(e.currentTarget, (x) => emit([x * 359.9, s || 0.01, v || 0.01]))(e)}>
        <i style={{ left: `${(h / 360) * 100}%` }} />
      </div>
      {alpha && (
        <div className="alpha" onPointerDown={(e) => drag(e.currentTarget, (x) => emit(hsv, Math.round(x * 100) / 100))(e)}
          style={{ ['--c' as string]: toHex({ ...c, a: 1 }, false) } as CSSProperties}>
          <i style={{ left: `${c.a * 100}%` }} />
        </div>
      )}
      <div className="row" style={{ gap: 6 }}>
        <input className="input hex" value={hex} spellCheck={false}
          onChange={(e) => {
            setHex(e.target.value)
            const m = e.target.value.trim().replace(/^#/, '')
            if (/^([0-9a-f]{3}|[0-9a-f]{6})$/i.test(m)) { const p = parseColor('#' + m); const hh = rgbToHsv(p.r, p.g, p.b); emit([hh[1] ? hh[0] : h, hh[1], hh[2]]) }
          }} />
        {alpha && <span className="faint" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>A {Math.round(c.a * 100)}%</span>}
      </div>
    </div>
  )
}

export function ColorChip({ value, onChange, label, alpha = true, style }: { value: string; onChange: (c: string) => void; label?: string; alpha?: boolean; style?: CSSProperties }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    window.addEventListener('mousedown', h)
    return () => window.removeEventListener('mousedown', h)
  }, [open])
  return (
    <div className="ie-chipwrap" ref={ref} style={style}>
      <button className="ie-chip" title={label} onClick={() => setOpen(!open)}>
        <span className="sw" style={{ background: value }} />{label && <span>{label}</span>}
      </button>
      {open && (
        <div className="ie-pop" onMouseDown={(e) => e.stopPropagation()}>
          <ColorPicker value={value} onChange={onChange} alpha={alpha} />
          <RecentSwatches onPick={onChange} />
        </div>
      )}
    </div>
  )
}

export function RecentSwatches({ onPick }: { onPick: (c: string) => void }) {
  const recent = useUI((s) => s.recent)
  const base = ['#000000', '#ffffff', '#ff3b30', '#ff9500', '#ffcc00', '#34c759', '#00c7be', '#007aff', '#5856d6', '#af52de', '#ff2d55', '#8e8e93']
  return (
    <div className="ie-swatches">
      {[...recent, ...base].slice(0, 24).map((c, i) => <button key={c + i} title={c} style={{ background: c }} onClick={() => onPick(c)} />)}
    </div>
  )
}

export function Section({ title, children, right, defaultOpen = true }: { title: string; children: ReactNode; right?: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="ie-section">
      <div className="hd" onClick={() => setOpen(!open)}><span className="chev">{open ? '▾' : '▸'}</span>{title}<span className="grow" />{right && <span onClick={(e) => e.stopPropagation()}>{right}</span>}</div>
      {open && <div className="bd">{children}</div>}
    </div>
  )
}
