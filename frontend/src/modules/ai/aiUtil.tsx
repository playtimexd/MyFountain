import { useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { Clip, Project } from '../../types/project'
import type { PanelProps } from '../../shared/panelTypes'
import { Callout } from '../../shared/ui'

/** PanelProps + a flag set by the standalone AI hub (no host editor). */
export type AiPanelProps = PanelProps & { standalone?: boolean }

export const allClips = (p?: Project): Clip[] => (p ? p.tracks.flatMap((t) => t.clips) : [])

/** Best guess of "the media the user means" inside a host editor: selected media > selected clip's asset > first video clip. */
export function guessMediaId(props: PanelProps): string | null {
  if (props.selection?.mediaIds?.length) return props.selection.mediaIds[0]
  const clips = allClips(props.project)
  if (props.selection?.clipIds?.length) {
    const c = clips.find((c) => c.id === props.selection!.clipIds[0] && c.assetId)
    if (c?.assetId) return c.assetId
  }
  const v = clips.find((c) => (c.type === 'video' || c.type === 'audio') && c.assetId)
  return v?.assetId || null
}

/** Clip(s) of a project that play `mediaId` (selected clip first). */
export function clipsOfMedia(props: PanelProps, mediaId: string | undefined | null): Clip[] {
  if (!mediaId) return []
  const cs = allClips(props.project).filter((c) => c.assetId === mediaId)
  const sel = props.selection?.clipIds || []
  return [...cs.filter((c) => sel.includes(c.id)), ...cs.filter((c) => !sel.includes(c.id))]
}

/** Map a [s,e] source-time range through a clip to timeline time (null if outside the clip's in/out window). */
export function mapRange(c: Clip, s: number, e: number): { start: number; end: number } | null {
  const sp = c.speed || 1
  const a = Math.max(s, c.in), b = Math.min(e, c.out)
  if (b - a < 0.03) return null
  return { start: c.start + (a - c.in) / sp, end: c.start + (b - c.in) / sp }
}

export function downloadText(name: string, text: string, mime = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type: mime + ';charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = name; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

export const fmtDur = (s: number) => {
  const m = Math.floor(s / 60), sec = s - m * 60
  return `${m}:${sec.toFixed(1).padStart(4, '0')}`
}

/** Collapsible block used inside narrow side panels. Uncontrolled by default; pass `open` + `onToggle` to control it. */
export function Section({ title, icon, children, defaultOpen = false, badge, open: openProp, onToggle, id }: {
  title: string; icon?: ReactNode; children: ReactNode; defaultOpen?: boolean; badge?: ReactNode
  open?: boolean; onToggle?: (open: boolean) => void; id?: string
}) {
  const [openState, setOpen] = useState(defaultOpen)
  const open = openProp ?? openState
  const toggle = () => { setOpen(!open); onToggle?.(!open) }
  return (
    <div className="panel ai-sec" data-sec={id} style={{ marginBottom: 8 }}>
      <button type="button" className="panel-title ai-sec-head" aria-expanded={open} onClick={toggle} style={{ borderBottom: open ? undefined : 'none' }}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}{icon}<span className="grow">{title}</span>{badge}
      </button>
      {open && <div style={{ padding: 10 }}>{children}</div>}
    </div>
  )
}

/** Segmented single-choice control (wraps in narrow panels). */
export function Seg<T extends string | number>({ value, options, onChange }: {
  value: T; options: { v: T; label: string; disabled?: boolean; title?: string }[]; onChange: (v: T) => void
}) {
  return (
    <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
      {options.map((o) => (
        <button key={String(o.v)} disabled={o.disabled} title={o.title} className={`btn sm ${o.v === value ? 'primary' : ''}`} onClick={() => onChange(o.v)}>{o.label}</button>
      ))}
    </div>
  )
}

export function Chips<T extends string>({ options, value, onChange }: { options: { v: T; label: string }[]; value: T | ''; onChange: (v: T | '') => void }) {
  return (
    <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
      {options.map((o) => (
        <button key={o.v} className="chip" onClick={() => onChange(value === o.v ? '' : o.v)}
          style={{ cursor: 'pointer', border: '1px solid ' + (value === o.v ? 'var(--accent)' : 'transparent'), background: value === o.v ? 'var(--accent-soft)' : undefined, color: value === o.v ? 'var(--text)' : undefined }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Note({ children, tone }: { children: ReactNode; tone?: 'warn' | 'ok' }) {
  if (tone) return <Callout tone={tone} style={{ marginTop: 6 }}>{children}</Callout>
  return <div style={{ fontSize: 12, marginTop: 6, color: 'var(--text-dim)', wordBreak: 'break-word' }}>{children}</div>
}
