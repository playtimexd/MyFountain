import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ImagePlus, RefreshCw } from 'lucide-react'
import { api } from '../../../api/client'
import type { MediaRow } from '../../../types/api'

export interface ProviderInfo {
  id: string
  name: string
  available: boolean
  needs_key: boolean
  key_set: boolean
  cost_note: string
  capabilities: Record<string, any>
  verified: boolean
  cloud?: boolean
  note?: string
  howto?: string
  running?: boolean
  workflows?: { id: string; name: string; ready: boolean; howto: string[]; download_gb: number }[]
  install_steps?: string[]
}

export interface Estimate {
  provider: string
  currency: string | null
  amount: number | null
  unit: string
  note: string
  [k: string]: unknown
}

export function useProviders(url: string) {
  const [items, setItems] = useState<ProviderInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const reload = useCallback(async () => {
    setLoading(true)
    try { setItems(await api.get<ProviderInfo[]>(url)); setErr(null) } catch (e: any) { setErr(e.message || String(e)) }
    setLoading(false)
  }, [url])
  useEffect(() => { reload() }, [reload])
  return { items, loading, err, reload }
}

export function Note({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'ok' | 'err'; children: ReactNode }) {
  const color = tone === 'warn' ? 'var(--warn)' : tone === 'ok' ? 'var(--ok)' : tone === 'err' ? 'var(--danger)' : 'var(--text-dim)'
  return (
    <div style={{ fontSize: 12, color, background: 'var(--bg)', border: '1px solid var(--line)', borderLeft: `3px solid ${color}`, borderRadius: 5, padding: '6px 8px', margin: '4px 0 10px', lineHeight: 1.5 }}>
      {children}
    </div>
  )
}

export function ProviderStatus({ p, reload, loading }: { p: ProviderInfo; reload: () => void; loading: boolean }) {
  const steps = p.install_steps && !p.running ? p.install_steps : null
  return (
    <div style={{ marginBottom: 8 }}>
      <Note tone={p.available ? 'ok' : 'warn'}>
        <div className="row" style={{ gap: 6 }}>
          <b className="grow">{p.available ? 'Ready' : p.needs_key && !p.key_set ? 'API key missing' : 'Not available'}: {p.name}</b>
          {!p.verified && <span className="chip" title="Request format could not be verified against the official docs">unverified</span>}
          <button className="btn ghost icon sm" onClick={reload} title="Re-check availability"><RefreshCw size={12} className={loading ? 'spin' : ''} /></button>
        </div>
        <div className="muted">{p.cost_note}</div>
        {p.note && <div className="faint">{p.note}</div>}
        {!p.available && p.howto && !steps && <div style={{ marginTop: 4 }}>{p.howto}{p.needs_key ? ' (gear icon in the top bar)' : ''}</div>}
        {steps && <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>{steps.map((s, i) => <li key={i}>{s.replace(/^\d+\.\s*/, '')}</li>)}</ul>}
        {p.workflows && p.running && !p.available && (
          <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
            {p.workflows.map((w) => <li key={w.id}><b>{w.name}</b>{w.ready ? ' - ready' : ` - ${w.howto.join(' | ')}`}</li>)}
          </ul>
        )}
      </Note>
    </div>
  )
}

/** Debounced cost estimate / request validation via the endpoint's dry_run mode (no network call on the server side). */
export function useEstimate(url: string, body: Record<string, unknown>, enabled: boolean) {
  const [est, setEst] = useState<Estimate | null>(null)
  const [req, setReq] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const key = JSON.stringify(body)
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) { setEst(null); setProblem(null); return }
    const my = ++seq.current
    const t = setTimeout(async () => {
      try {
        const r = await api.post<{ estimate: Estimate; requests: unknown[] }>(url, { ...body, dry_run: true })
        if (my !== seq.current) return
        setEst(r.estimate); setReq(r.requests); setProblem(null)
      } catch (e: any) {
        if (my !== seq.current) return
        setEst(null); setReq(null); setProblem(e.message || String(e))
      }
    }, 450)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, key, enabled])
  return { est, req, problem }
}

export function fmtEstimate(e: Estimate | null): string {
  if (!e) return ''
  if (e.amount === 0) return 'free'
  if (e.amount == null) return 'cost unknown'
  return e.currency === 'USD' ? `~$${e.amount.toFixed(2)}` : `~${e.amount} ${e.currency ?? ''}`
}

/** Library image chooser with upload. `value` is a media id. */
export function ImagePicker({ value, onChange, label }: { value: string | null; onChange: (m: MediaRow | null) => void; label: string }) {
  const [items, setItems] = useState<MediaRow[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const reload = useCallback(async () => {
    try { setItems(await api.get<MediaRow[]>('/api/media?kind=image&limit=300')) } catch { /* engine offline */ }
  }, [])
  useEffect(() => { reload() }, [reload])
  const cur = items.find((m) => m.id === value) || null
  const upload = async (f: File) => {
    setBusy(true); setErr(null)
    try {
      const row = await api.upload<MediaRow>('/api/media/upload', f)
      setItems((x) => [row, ...x.filter((i) => i.id !== row.id)])
      onChange(row)
    } catch (e: any) { setErr(e.message || String(e)) }
    setBusy(false)
  }
  return (
    <div className="field">
      <label>{label}</label>
      <div className="row" style={{ alignItems: 'stretch' }}>
        <div style={{ width: 64, height: 40, flex: 'none', background: '#000', borderRadius: 4, overflow: 'hidden', border: '1px solid var(--line)' }}>
          {cur && <img src={cur.thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />}
        </div>
        <select className="select grow" value={value || ''} onChange={(e) => onChange(items.find((m) => m.id === e.target.value) || null)}>
          <option value="">None</option>
          {items.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.width}x{m.height})</option>)}
        </select>
        <button className="btn icon" onClick={() => file.current?.click()} title="Upload an image into the library" disabled={busy}><ImagePlus size={14} /></button>
        <button className="btn icon" onClick={reload} title="Refresh list"><RefreshCw size={13} /></button>
        <input ref={file} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  )
}

export function CostConfirm({ checked, onChange, text, est }: { checked: boolean; onChange: (v: boolean) => void; text: string; est: Estimate | null }) {
  return (
    <label className="row" style={{ alignItems: 'flex-start', margin: '4px 0 10px', cursor: 'pointer', fontSize: 12 }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 2 }} />
      <span>
        <b>{text}</b>{est ? <> Estimated: <b>{fmtEstimate(est)}</b>.</> : null}
        {est?.note && <span className="faint" style={{ display: 'block' }}>{est.note}</span>}
      </span>
    </label>
  )
}
