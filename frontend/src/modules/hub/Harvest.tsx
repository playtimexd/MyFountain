import { useEffect, useState } from 'react'
import { Play, RotateCcw } from 'lucide-react'
import { api, fmtSize, useJob } from '../../api/client'
import { Field, JobBar } from '../../shared/ui'
import type { HarvestState, HubKind, Policy, SourceInfo } from './api'
import { KINDS } from './api'
import { toast } from './common'

interface HarvestRow { id: string; status: string; params: HarvestState['params']; totals: HarvestState['totals']; created: number; updated: number }

export default function Harvest({ sources, onChanged }: { sources: SourceInfo[]; onChanged: () => void }) {
  const auto = sources.filter((s) => s.automated)
  const [picked, setPicked] = useState<Set<string>>(new Set(['polyhaven', 'ambientcg']))
  const [queries, setQueries] = useState('')
  const [kinds, setKinds] = useState<Set<HubKind>>(new Set())
  const [maxItems, setMaxItems] = useState(30)
  const [maxGb, setMaxGb] = useState(1)
  const [policy, setPolicy] = useState<Policy>('commercial')
  const [res, setRes] = useState('1k')
  const [history, setHistory] = useState<HarvestRow[]>([])
  const job = useJob<HarvestState>()
  const st = job.job?.result as HarvestState | null | undefined

  const loadHistory = () => api.get<HarvestRow[]>('/api/hub/harvest').then(setHistory).catch(() => {})
  useEffect(() => { loadHistory() }, [])

  const toggle = <T,>(set: Set<T>, v: T, fn: (s: Set<T>) => void) => { const n = new Set(set); if (n.has(v)) n.delete(v); else n.add(v); fn(n) }
  const availableKinds = KINDS.filter((k) => [...picked].some((s) => sources.find((x) => x.id === s)?.kinds.includes(k.id)))

  const start = async (startReq: () => Promise<any>) => {
    try { await job.run(startReq); toast('Harvest finished') } catch { /* JobBar shows it */ } finally { loadHistory(); onChanged() }
  }
  const submit = () => start(() => api.post('/api/hub/harvest', {
    sources: [...picked], queries: queries.split('\n').map((s) => s.trim()).filter(Boolean), kinds: [...kinds],
    max_items: maxItems, max_gb: maxGb, license_policy: policy, variants: { texture_res: res },
  }))
  const resume = (id: string) => start(() => api.post(`/api/hub/harvest/${id}/resume`))

  return (
    <div className="hub-scroll" style={{ maxWidth: 1100 }}>
      <div className="hub-section">
        <h3>Harvest job</h3>
        <p className="hub-note" style={{ marginTop: 0 }}>Downloads many assets in one go from the official APIs. Caps stop the job; it can be cancelled and resumed later.
          Sources without a key, and catalog-only sources (Kenney, Quaternius, Mixamo, Truebones), are skipped - import their packs manually.</p>
        <Field label="Sources">
          <div className="hub-pill-row">
            {auto.map((s) => (
              <button key={s.id} className={`hub-pill${picked.has(s.id) ? ' on' : ''}`} disabled={!s.available && !picked.has(s.id)}
                title={s.available ? s.terms : `${s.note} - add a key under Sources & keys`} onClick={() => toggle(picked, s.id, setPicked)}>
                {s.name}{!s.available ? ' (needs key)' : ''}</button>))}
          </div>
        </Field>
        <div className="hub-form">
          <Field label="Search queries (one per line; empty = most popular)">
            <textarea className="textarea" rows={4} value={queries} onChange={(e) => setQueries(e.target.value)} placeholder={'tree\nrock\nfootsteps\nmedieval'} />
          </Field>
          <Field label="Kinds (none = everything the source has)">
            <div className="hub-pill-row">
              {availableKinds.map((k) => <button key={k.id} className={`hub-pill${kinds.has(k.id) ? ' on' : ''}`} onClick={() => toggle(kinds, k.id, setKinds)}>{k.label}</button>)}
            </div>
          </Field>
        </div>
        <div className="hub-form">
          <Field label={`Max items (total): ${maxItems}`}><input type="range" min={1} max={500} value={maxItems} onChange={(e) => setMaxItems(+e.target.value)} /></Field>
          <Field label="Max download size (GB)"><input className="input" type="number" min={0.01} step={0.5} value={maxGb} onChange={(e) => setMaxGb(+e.target.value)} /></Field>
          <Field label="Licence policy">
            <select className="select" value={policy} onChange={(e) => setPolicy(e.target.value as Policy)}>
              <option value="cc0">CC0 only (no attribution needed)</option>
              <option value="commercial">Commercial-safe: CC0 + CC-BY (attribution stored)</option>
              <option value="any">Any - includes non-commercial / no-derivatives</option>
            </select>
          </Field>
          <Field label="Texture / HDRI resolution">
            <select className="select" value={res} onChange={(e) => setRes(e.target.value)}><option value="1k">1K (small)</option><option value="2k">2K</option><option value="4k">4K (large)</option></select>
          </Field>
        </div>
        <div className="row">
          <button className="btn primary" disabled={job.busy || picked.size === 0} onClick={submit}><Play size={13} />Start harvest</button>
          <span className="hub-note">The item cap is split evenly between the selected sources. CMU mocap is capped at 40 clips per harvest (CMU asks not to crawl its database).</span>
        </div>
        <JobBar job={job.job} error={job.error} onCancel={job.cancel} />
      </div>

      {st && st.sources && (
        <div className="hub-section">
          <h3>Progress · {st.status} · {st.totals.added} added · {fmtSize(st.totals.bytes)}</h3>
          {Object.entries(st.sources).map(([sid, s]) => (
            <div key={sid} className="hub-progress-src">
              <b>{sources.find((x) => x.id === sid)?.name ?? sid}</b>
              <div className="progress"><i style={{ width: `${Math.min(100, (s.added / Math.max(1, s.quota)) * 100)}%` }} /></div>
              <span className="muted">{s.added}/{s.quota} added · {s.exists} existing · {s.duplicates} dup · {s.skipped} skipped · {s.errors} err · {fmtSize(s.bytes)}{s.note ? ` · ${s.note}` : ''}</span>
            </div>))}
          {st.recent?.length > 0 && (
            <div className="row" style={{ flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
              {st.recent.map((r) => (
                <div key={r.id} title={`${r.name} · ${r.license}`} style={{ width: 64 }}>
                  {r.thumb ? <img src={r.thumb} style={{ width: 64, height: 48, objectFit: 'cover', borderRadius: 4, background: '#000' }} alt="" /> : <div style={{ width: 64, height: 48, background: 'var(--bg-3)', borderRadius: 4 }} />}
                  <div className="faint" style={{ fontSize: 10, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.name}</div>
                </div>))}
            </div>)}
          {st.log?.length > 0 && <div className="hub-log" style={{ marginTop: 10 }}>{st.log.slice(-40).join('\n')}</div>}
        </div>
      )}

      <div className="hub-section">
        <h3>Previous harvests</h3>
        {history.length === 0 ? <div className="faint">None yet.</div> : (
          <table className="hub-table"><thead><tr><th>When</th><th>Sources</th><th>Queries</th><th>Policy</th><th>Result</th><th>Status</th><th /></tr></thead>
            <tbody>{history.map((h) => (
              <tr key={h.id}>
                <td>{new Date(h.created * 1000).toLocaleString()}</td>
                <td>{h.params?.sources?.map((s) => sources.find((x) => x.id === s)?.name ?? s).join(', ')}</td>
                <td>{h.params?.queries?.filter(Boolean).join(', ') || <span className="faint">popular</span>}</td>
                <td>{h.params?.license_policy}</td>
                <td>{h.totals?.added ?? 0} added · {fmtSize(h.totals?.bytes ?? 0)} / {h.params?.max_items} max</td>
                <td>{h.status}</td>
                <td>{h.status !== 'running' && h.status !== 'queued' && (h.totals?.added ?? 0) < (h.params?.max_items ?? 0) &&
                  <button className="btn sm" disabled={job.busy} onClick={() => resume(h.id)}><RotateCcw size={12} />Resume</button>}</td>
              </tr>))}</tbody></table>)}
      </div>
    </div>
  )
}
