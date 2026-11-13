import { useRef } from 'react'
import type { ReactNode } from 'react'
import type { AnimClip } from './animApi'
import { METRIC_LABELS, fmtScore, scoreColor } from './animApi'

export function Note({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'ok' | 'err'; children: ReactNode }) {
  const color = tone === 'warn' ? 'var(--warn)' : tone === 'ok' ? 'var(--ok)' : tone === 'err' ? 'var(--danger)' : 'var(--text-dim)'
  return <div style={{ fontSize: 12, color, border: `1px solid ${color}`, borderRadius: 6, padding: '6px 10px', margin: '8px 0', background: 'var(--bg)', wordBreak: 'break-word' }}>{children}</div>
}

/** Hover-looping clip preview (mp4 on a dark background; falls back to the poster / a placeholder). */
export function ClipPreview({ clip, height = 110 }: { clip: AnimClip; height?: number }) {
  const v = useRef<HTMLVideoElement>(null)
  const src = clip.files.mp4
  return (
    <div style={{ height, background: '#14161c', position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}
      onMouseEnter={() => { v.current?.play().catch(() => undefined) }}
      onMouseLeave={() => { if (v.current) { v.current.pause(); v.current.currentTime = 0 } }}>
      {src
        ? <video ref={v} src={src} poster={clip.files.png} muted loop playsInline preload="metadata" style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }} />
        : <span className="faint" style={{ fontSize: 11 }}>no preview yet</span>}
      <span className="chip" style={{ position: 'absolute', left: 4, top: 4, background: 'rgba(0,0,0,.6)', fontSize: 10 }}>{clip.kind === 'retargeted' ? 'retargeted' : 'source'}</span>
      {clip.score != null && <span className="chip" style={{ position: 'absolute', right: 4, top: 4, background: 'rgba(0,0,0,.6)', color: scoreColor(clip.score), fontSize: 10 }}>{fmtScore(clip.score)}</span>}
      <span className="chip" style={{ position: 'absolute', right: 4, bottom: 4, background: 'rgba(0,0,0,.6)', fontSize: 10 }}>{clip.duration.toFixed(1)}s</span>
    </div>
  )
}

export function MetricsTable({ metrics }: { metrics: Record<string, any> }) {
  const rows = Object.entries(METRIC_LABELS).filter(([k]) => typeof metrics[k] === 'number')
  if (!rows.length) return null
  const bad = (k: string, v: number) => (k === 'foot_slide_pct_height_per_s' ? v > 15 : k === 'ground_penetration_max_pct_height' ? v > 3 : k === 'floating_mean_pct_height' ? v > 3 : k === 'jitter_p95_deg' ? v > 5 : k === 'loop_seam_vs_step' ? v > 2.5 : k === 'bone_stretch_max_pct' ? v > 2 : false)
  return (
    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
      <tbody>
        {rows.map(([k, [label, unit]]) => (
          <tr key={k} style={{ borderBottom: '1px solid var(--line)' }}>
            <td className="muted" style={{ padding: '3px 0' }}>{label}</td>
            <td style={{ textAlign: 'right', color: bad(k, metrics[k]) ? 'var(--warn)' : undefined }}>{metrics[k].toFixed(2)} <span className="faint">{unit}</span></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function Issues({ items }: { items: { severity?: string; message?: string; suggestion?: string; code?: string }[] }) {
  if (!items?.length) return <Note tone="ok">No issues found by the numeric checks.</Note>
  return (
    <div>
      {items.map((it, i) => (
        <div key={i} style={{ fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--line)' }}>
          <span className="chip" style={{ color: it.severity === 'high' ? 'var(--danger)' : it.severity === 'medium' ? 'var(--warn)' : 'var(--text-dim)', marginRight: 6 }}>{it.severity || 'info'}</span>
          {it.message}{it.suggestion ? <span className="faint"> — {it.suggestion}</span> : null}
        </div>
      ))}
    </div>
  )
}

export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-title"><span className="grow">{title}</span>{right}</div>
      <div style={{ padding: 12 }}>{children}</div>
    </div>
  )
}
