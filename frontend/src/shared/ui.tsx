import type { ComponentType, ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, Loader2, X, XCircle } from 'lucide-react'
import type { JobInfo } from '../types/api'

export function JobBar({ job, error, onCancel }: { job: JobInfo | null; error?: string | null; onCancel?: () => void }) {
  if (!job && !error) return null
  const running = job && (job.status === 'running' || job.status === 'queued')
  return (
    <div style={{ marginTop: 8 }}>
      {job && (
        <>
          <div className="row" style={{ marginBottom: 4, gap: 6 }}>
            {running && <Loader2 size={13} className="spin" style={{ color: 'var(--accent-text)' }} />}
            <span className="grow muted" style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {job.title}{job.message ? ` · ${job.message}` : ''}
            </span>
            <span className="faint" style={{ fontSize: 11, fontFamily: 'var(--mono)' }}>{Math.round(job.progress * 100)}%</span>
            {running && onCancel && <button className="btn ghost icon sm" onClick={onCancel} title="Cancel"><X size={12} /></button>}
          </div>
          <div className="progress"><i style={{ width: `${job.progress * 100}%` }} /></div>
        </>
      )}
      {error && <Callout tone="err" style={{ marginTop: 6 }}>{error}</Callout>}
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="field"><label>{label}</label>{children}</div>
}

type Icon = ComponentType<{ size?: number | string; className?: string }>

/** Empty state. Plain `<Empty>text</Empty>` still works; add icon / title / action for a richer block. */
export function Empty({ children, icon: I, title, action, compact }: { children?: ReactNode; icon?: Icon; title?: ReactNode; action?: ReactNode; compact?: boolean }) {
  return (
    <div className="ui-empty" style={compact ? { padding: '16px 12px' } : undefined}>
      {I && <div className="ico"><I size={18} /></div>}
      {title && <div className="t">{title}</div>}
      {children && <div className="h">{children}</div>}
      {action && <div className="a">{action}</div>}
    </div>
  )
}

export function Loading({ children = 'Loading…' }: { children?: ReactNode }) {
  return <div className="ui-loading"><Loader2 size={14} className="spin" />{children}</div>
}

/** Inline message box: neutral info, warning, error or success. */
export function Callout({ tone, children, style }: { tone?: 'warn' | 'err' | 'ok'; children: ReactNode; style?: React.CSSProperties }) {
  const I = tone === 'warn' ? AlertTriangle : tone === 'err' ? XCircle : tone === 'ok' ? CheckCircle2 : Info
  return <div className={`ui-callout ${tone || ''}`} style={style}><I size={14} /><div className="grow">{children}</div></div>
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd>{children}</kbd>
}

/** Two-column keyboard shortcut list: [['Space'], 'Play / pause'], [['Ctrl', 'Z'], 'Undo'] … */
export function Shortcuts({ items }: { items: [string[], string][] }) {
  return (
    <div className="ui-shortcuts">
      {items.map(([keys, label]) => [
        <span key={label + 'k'}>{keys.map((k) => <kbd key={k}>{k}</kbd>)}</span>,
        <span key={label}>{label}</span>,
      ])}
    </div>
  )
}

export interface SideNavItem<T extends string> { id: T; label: string; desc?: string; icon: Icon }

/** Vertical navigation for hub pages (AI Studio, Animation, 3D Models). */
export function SideNav<T extends string>({ items, value, onChange, head, foot }: {
  items: readonly SideNavItem<T>[]; value: T; onChange: (id: T) => void; head?: ReactNode; foot?: ReactNode
}) {
  return (
    <nav className="side-nav">
      {head && <div className="sn-head">{head}</div>}
      {items.map((n) => (
        <button key={n.id} className={`sn-item ${value === n.id ? 'on' : ''}`} onClick={() => onChange(n.id)} title={n.desc}>
          <n.icon size={15} />
          <span className="grow"><span className="sn-label">{n.label}</span>{n.desc && <span className="sn-desc">{n.desc}</span>}</span>
        </button>
      ))}
      {foot && <div className="sn-foot">{foot}</div>}
    </nav>
  )
}
