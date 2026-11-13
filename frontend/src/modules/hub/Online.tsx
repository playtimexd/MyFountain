import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Download, ExternalLink, PackageOpen, Plus, Search } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { Empty, JobBar } from '../../shared/ui'
import type { Candidate, OnlinePage, Policy, SourceInfo } from './api'
import { KINDS } from './api'
import { Badges, Thumb, toast } from './common'

interface AddResult { added: { id: string; name: string }[]; exists: unknown[]; duplicates: unknown[]; skipped: { name: string; reason: string }[]; errors: { name: string; error: string }[] }

export default function Online({ sources, onImport, onChanged }: {
  sources: SourceInfo[]; onImport: (source: string, license: string, packName?: string) => void; onChanged: () => void
}) {
  const [src, setSrc] = useState('polyhaven')
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [policy, setPolicy] = useState<Policy>('commercial')
  const [res, setRes] = useState('1k')
  const [items, setItems] = useState<Candidate[]>([])
  const [page, setPage] = useState(1)
  const [more, setMore] = useState(false)
  const [total, setTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const add = useJob<AddResult>()
  const sentinel = useRef<HTMLDivElement>(null)
  const reqId = useRef(0)
  const info = sources.find((s) => s.id === src)

  const search = useCallback(async (p: number) => {
    const id = ++reqId.current
    setLoading(true); setErr(null)
    try {
      const qs = new URLSearchParams({ source: src, q, kind, page: String(p), policy })
      const r = await api.get<OnlinePage>(`/api/hub/online?${qs}`)
      if (id !== reqId.current) return
      setItems((o) => (p === 1 ? r.items : [...o, ...r.items]))
      setPage(p); setMore(r.has_more); setTotal(r.total)
    } catch (e: any) { if (id === reqId.current) { setErr(e.message); setMore(false); if (p === 1) setItems([]) } } finally { if (id === reqId.current) setLoading(false) }
  }, [src, q, kind, policy])

  useEffect(() => { setSel(new Set()); setMore(false); setItems([]); search(1) }, [src, kind, policy]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting && more && !loading) search(page + 1) }, { rootMargin: '300px' })
    io.observe(el)
    return () => io.disconnect()
  }, [more, loading, page, search])

  const keyOf = (c: Candidate) => `${c.source}:${c.source_id}`
  const toggle = (c: Candidate) => setSel((s) => { const n = new Set(s); const k = keyOf(c); if (n.has(k)) n.delete(k); else n.add(k); return n })
  const addItems = async (list: Candidate[]) => {
    const todo = list.filter((c) => !c.manual && !c.in_hub)
    if (!todo.length) return toast('Nothing to add (already in the hub or manual-only).')
    try {
      const j = await add.run(() => api.post('/api/hub/add', { items: todo.map((c) => ({ source: c.source, source_id: c.source_id, name: c.name })), variants: { texture_res: res }, policy }))
      const r = j.result
      if (r) {
        toast(`Added ${r.added.length}` + (r.exists.length ? ` · ${r.exists.length} already there` : '') + (r.duplicates.length ? ` · ${r.duplicates.length} duplicates` : '') +
          (r.skipped.length ? ` · skipped ${r.skipped.length} (${r.skipped[0].reason})` : '') + (r.errors.length ? ` · ${r.errors.length} failed: ${r.errors[0].error}` : ''))
        const addedIds = new Set(todo.map(keyOf))
        setItems((o) => o.map((c) => (addedIds.has(keyOf(c)) ? { ...c, in_hub: c.in_hub ?? 'yes' } : c)))
        setSel(new Set()); onChanged()
      }
    } catch { /* shown by JobBar */ }
  }

  const kinds = KINDS.filter((k) => !info || info.kinds.includes(k.id))
  const policyMatters = src === 'freesound' || src === 'sketchfab'

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
      <div className="hub-bar">
        <div className="hub-pill-row">
          {sources.filter((s) => s.id !== 'local').map((s) => (
            <button key={s.id} className={`hub-pill${src === s.id ? ' on' : ''}`} onClick={() => { setSrc(s.id); setKind('') }}
              title={`${s.note}${s.automated ? '' : ' (catalog only)'}`}>{s.name}{!s.available ? ' (key)' : ''}</button>))}
        </div>
      </div>
      <div className="hub-bar">
        <Search size={14} className="faint" />
        <input className="input" style={{ flex: 1, minWidth: 160 }} placeholder={`Search ${info?.name ?? ''}…`} value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { setSel(new Set()); search(1) } }} />
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All kinds</option>{kinds.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select>
        <select className="select" value={policy} onChange={(e) => setPolicy(e.target.value as Policy)} title="Licence policy used for filtering and when adding">
          <option value="cc0">CC0 only</option><option value="commercial">Commercial-safe (CC0 + CC-BY)</option><option value="any">Any (incl. NC / ND)</option>
        </select>
        <select className="select" value={res} onChange={(e) => setRes(e.target.value)} title="Texture / HDRI resolution to download"><option value="1k">1K</option><option value="2k">2K</option><option value="4k">4K</option></select>
        <button className="btn primary" onClick={() => { setSel(new Set()); search(1) }}><Search size={13} />Search</button>
      </div>
      {info && (
        <div className="hub-bar" style={{ background: 'transparent' }}>
          <span className="hub-note" style={{ flex: 1 }}>{info.terms} {info.terms_url && <a href={info.terms_url} target="_blank" rel="noreferrer">Terms <ExternalLink size={10} /></a>}
            {policyMatters ? '' : ' · Licence policy only marks results here; it is enforced when adding.'}</span>
          {!info.automated && <button className="btn sm" onClick={() => onImport(info.id, info.default_license)}><PackageOpen size={12} />Import pack zip</button>}
        </div>
      )}
      {sel.size > 0 && (
        <div className="hub-selbar">
          <b>{sel.size} selected</b>
          <button className="btn sm primary" disabled={add.busy} onClick={() => addItems(items.filter((c) => sel.has(keyOf(c))))}><Download size={12} />Add selected to hub</button>
          <button className="btn sm" onClick={() => setSel(new Set(items.filter((c) => !c.manual && !c.in_hub && c.allowed !== false).map(keyOf)))}>Select all addable</button>
          <button className="btn sm ghost" onClick={() => setSel(new Set())}>Clear</button>
        </div>
      )}
      <div style={{ padding: '0 16px' }}><JobBar job={add.job} error={add.error} onCancel={add.cancel} /></div>
      <div className="hub-scroll">
        {err && <div className="err" style={{ marginBottom: 8 }}>{err}</div>}
        {!loading && !err && items.length === 0 && <Empty>No results.</Empty>}
        <div className="hub-grid">
          {items.map((c) => {
            const k = keyOf(c)
            const blocked = c.allowed === false
            return (
              <div key={k} className={`hub-card${sel.has(k) ? ' sel' : ''}`} style={blocked ? { opacity: 0.55 } : undefined}
                onClick={() => (!c.manual && !c.in_hub ? toggle(c) : undefined)}>
                {!c.manual && !c.in_hub && <div className={`check${sel.has(k) ? ' on' : ''}`}>{sel.has(k) && <Check size={12} />}</div>}
                {c.in_hub && <span className="src" style={{ color: 'var(--ok)' }}>In hub</span>}
                <Thumb src={c.preview_url} kind={c.kind} audio={(c.extra?.audio_preview as string) || null} cover={c.kind === 'material' || c.kind === 'hdri'} />
                <div className="meta">
                  <div className="name" title={c.name}>{c.name}</div>
                  <Badges kind={c.kind} license={c.license} cls={c.license_class} rigged={!!c.stats?.rigged} animated={!!c.stats?.animated || !!c.stats?.animation_count} />
                  <div className="faint" style={{ fontSize: 10, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {[c.author ? `by ${c.author}` : '', c.stats?.polycount ? `${Number(c.stats.polycount).toLocaleString()} tris` : '', c.stats?.duration ? `${Number(c.stats.duration).toFixed(1)}s` : ''].filter(Boolean).join(' · ')}
                  </div>
                  <div className="row" style={{ gap: 4 }}>
                    {c.manual ? (
                      <>
                        {c.source_url && <a className="btn sm" href={c.source_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}><ExternalLink size={11} />Open page</a>}
                        <button className="btn sm" onClick={(e) => { e.stopPropagation(); onImport(c.source, c.license, c.name) }}><PackageOpen size={11} />Import zip</button>
                      </>
                    ) : c.in_hub ? <span className="faint" style={{ fontSize: 11 }}>Added</span> : (
                      <button className="btn sm" disabled={add.busy || blocked} title={blocked ? 'Excluded by the licence policy' : 'Download into the hub'}
                        onClick={(e) => { e.stopPropagation(); addItems([c]) }}><Plus size={11} />Add to hub</button>
                    )}
                    {!c.manual && c.source_url && <a className="btn sm ghost icon" href={c.source_url} target="_blank" rel="noreferrer" title="Open on the source site" onClick={(e) => e.stopPropagation()}><ExternalLink size={11} /></a>}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
        <div ref={sentinel} className="hub-sentinel">{loading ? 'Searching…' : more ? '' : items.length ? `${items.length}${total ? ` of ${total}` : ''} shown` : ''}</div>
      </div>
    </div>
  )
}
