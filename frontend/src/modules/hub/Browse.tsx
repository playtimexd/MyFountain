import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronRight, Search, Send, Trash2, X } from 'lucide-react'
import { api, fmtSize } from '../../api/client'
import { Empty } from '../../shared/ui'
import type { AssetPage, CategoryNode, Facets, HubAsset } from './api'
import { KINDS, LICENSE_CLASSES, categoryTrail, kindLabel, rollUp } from './api'
import CategoryTree from './CategoryTree'
import { Badges, Thumb, audioUrl, toast, useAssetActions } from './common'
import AssetDetail from './AssetDetail'

interface Filters { q: string; category: string; collection: string; kind: string; source: string; license: string; format: string; rigged: boolean; animated: boolean; sort: string }
const EMPTY: Filters = { q: '', category: '', collection: '', kind: '', source: '', license: '', format: '', rigged: false, animated: false, sort: '' }
const CAT_KEY = 'cutlab.hub.category'
const initialFilters = (): Filters => { try { return { ...EMPTY, category: localStorage.getItem(CAT_KEY) || '' } } catch { return EMPTY } }

export default function Browse({ sourceNames, onCredits, refreshKey }: { sourceNames: Record<string, string>; onCredits: (ids: string[]) => void; refreshKey: number }) {
  const [f, setF] = useState<Filters>(initialFilters)
  const [tree, setTree] = useState<CategoryNode[]>([])
  const [allCols, setAllCols] = useState(false)
  const [qInput, setQInput] = useState('')
  const [items, setItems] = useState<HubAsset[]>([])
  const [facets, setFacets] = useState<Facets | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const reqId = useRef(0)
  const { sendToEngine } = useAssetActions()

  const load = useCallback(async (p: number, filters: Filters) => {
    const id = ++reqId.current
    setLoading(true); setErr(null)
    const qs = new URLSearchParams({ page: String(p), page_size: '48' })
    Object.entries(filters).forEach(([k, v]) => { if (v) qs.set(k, String(v)) })
    try {
      const r = await api.get<AssetPage>(`/api/hub/assets?${qs}`)
      if (id !== reqId.current) return
      setItems((old) => (p === 1 ? r.items : [...old, ...r.items.filter((x) => !old.some((o) => o.id === x.id))]))
      setFacets(r.facets); setTotal(r.total); setPage(p); setMore(r.has_more)
    } catch (e: any) { if (id === reqId.current) { setErr(e.message); setMore(false) } } finally { if (id === reqId.current) setLoading(false) }
  }, [])

  useEffect(() => { load(1, f) }, [f, load, refreshKey])
  useEffect(() => { api.get<{ tree: CategoryNode[] }>('/api/hub/categories').then((r) => setTree(r.tree)).catch(() => {}) }, [refreshKey])
  useEffect(() => { try { localStorage.setItem(CAT_KEY, f.category) } catch { /* ignore */ } }, [f.category])
  const catCounts = useMemo(() => rollUp(facets?.category), [facets])
  const catTotal = useMemo(() => Object.values(facets?.category ?? {}).reduce((a, b) => a + b, 0), [facets])
  const trail = useMemo(() => (f.category ? categoryTrail(tree, f.category) : []), [tree, f.category])
  useEffect(() => { const t = setTimeout(() => setF((o) => (o.q === qInput ? o : { ...o, q: qInput })), 350); return () => clearTimeout(t) }, [qInput])

  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting && more && !loading) load(page + 1, f) }, { rootMargin: '400px' })
    io.observe(el)
    return () => io.disconnect()
  }, [more, loading, page, f, load])

  const toggle = (id: string) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const set = (patch: Partial<Filters>) => setF((o) => ({ ...o, ...patch }))
  const delSelected = async () => {
    if (!confirm(`Delete ${sel.size} asset(s) and their downloaded files from the hub? This cannot be undone.`)) return
    for (const id of sel) { try { await api.del(`/api/hub/assets/${id}`) } catch { /* keep going */ } }
    setSel(new Set()); load(1, f); toast('Deleted')
  }

  const facet = (title: string, key: 'kind' | 'source' | 'license' | 'format' | 'collection', entries: [string, string][], limit = 0) => {
    const counts = (facets?.[key] ?? {}) as Record<string, number>
    let rows = entries.filter(([id]) => counts[id] || f[key] === id)
    if (!rows.length) return null
    const more = limit > 0 && rows.length > limit && !allCols
    if (more) rows = rows.filter(([id], i) => i < limit || f[key] === id)
    return (
      <>
        <h4>{title}</h4>
        {rows.map(([id, label]) => (
          <div key={id} className={`facet${f[key] === id ? ' on' : ''}`} onClick={() => set({ [key]: f[key] === id ? '' : id } as Partial<Filters>)}>
            <span className="cat-label" title={label}>{label}</span><span className="n">{(counts[id] ?? 0).toLocaleString()}</span>
          </div>))}
        {limit > 0 && entries.filter(([id]) => counts[id]).length > limit && <button className="btn sm ghost link" style={{ marginTop: 2 }} onClick={() => setAllCols((v) => !v)}>{more ? 'Show all' : 'Show fewer'}</button>}
      </>
    )
  }

  return (
    <div className="hub-body">
      <aside className="hub-side">
        <h4>Categories</h4>
        <CategoryTree tree={tree} counts={catCounts} total={catTotal} value={f.category} onChange={(category) => set({ category })} />
        {facet('Collection', 'collection', Object.entries(facets?.collection ?? {}).sort((a, b) => b[1] - a[1]).map(([c]) => [c, c]), 8)}
        {facet('Kind', 'kind', KINDS.map((k) => [k.id, k.label]))}
        {facet('Licence', 'license', LICENSE_CLASSES.map((l) => [l.id, l.label]))}
        <h4>Rig / motion</h4>
        <div className={`facet${f.rigged ? ' on' : ''}`} onClick={() => set({ rigged: !f.rigged })}>Rigged<span className="n">{facets?.rigged ?? 0}</span></div>
        <div className={`facet${f.animated ? ' on' : ''}`} onClick={() => set({ animated: !f.animated })}>Animated<span className="n">{facets?.animated ?? 0}</span></div>
        {facet('Source', 'source', Object.keys(facets?.source ?? {}).map((s) => [s, sourceNames[s] ?? s]))}
        {facet('Format', 'format', Object.keys(facets?.format ?? {}).sort().map((s) => [s, s.toUpperCase()]))}
        {JSON.stringify({ ...f, q: '', sort: '' }) !== JSON.stringify({ ...EMPTY }) && <button className="btn sm ghost" style={{ marginTop: 10 }} onClick={() => setF({ ...EMPTY, q: f.q })}><X size={12} />Clear filters</button>}
      </aside>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {trail.length > 0 && (
          <div className="hub-crumbs">
            <span className="crumb" onClick={() => set({ category: '' })}>All assets</span>
            {trail.map((n) => <span key={n.id} className="row" style={{ gap: 4 }}><ChevronRight size={12} className="faint" />
              <span className={`crumb${n.id === f.category ? ' on' : ''}`} onClick={() => set({ category: n.id })}>{n.label}</span></span>)}
            {(trail[trail.length - 1]?.children ?? []).filter((c) => (catCounts[c.id] ?? 0) > 0).length > 0 && <span className="grow" />}
            <span className="hub-pill-row">
              {(trail[trail.length - 1]?.children ?? []).filter((c) => (catCounts[c.id] ?? 0) > 0).map((c) =>
                <button key={c.id} className="hub-pill" onClick={() => set({ category: c.id })}>{c.label} <span className="faint">{catCounts[c.id]}</span></button>)}
            </span>
          </div>
        )}
        <div className="hub-bar">
          <Search size={14} className="faint" />
          <input className="input" style={{ flex: 1, minWidth: 180 }} placeholder="Search the hub (keywords + semantic: e.g. 'mossy stone wall', 'zombie walk')" value={qInput} onChange={(e) => setQInput(e.target.value)} />
          <select className="select" value={f.sort} onChange={(e) => set({ sort: e.target.value })} disabled={!!f.q} title="Sorting applies when there is no search text">
            <option value="">Newest</option><option value="oldest">Oldest</option><option value="name">Name</option><option value="size">Largest</option>
          </select>
          <span className="faint" style={{ fontSize: 12 }}>{total} asset{total === 1 ? '' : 's'}</span>
        </div>
        {sel.size > 0 && (
          <div className="hub-selbar">
            <b>{sel.size} selected</b>
            <button className="btn sm" onClick={() => setSel(new Set(items.map((i) => i.id)))}>Select all loaded</button>
            <button className="btn sm" onClick={() => onCredits([...sel])}>Credits for selection</button>
            <button className="btn sm" onClick={() => sendToEngine([...sel])}><Send size={12} />Send to my-engine</button>
            <button className="btn sm danger" onClick={delSelected}><Trash2 size={12} />Delete</button>
            <button className="btn sm ghost" onClick={() => setSel(new Set())}><X size={12} />Clear</button>
          </div>
        )}
        <div className="hub-scroll">
          {err && <div className="err">{err}</div>}
          {!loading && items.length === 0 && !err && (
            <Empty>{f.q || f.kind || f.source || f.license || f.category || f.collection ? 'Nothing matches these filters.' : 'The hub is empty. Use Online search to add assets, run a Harvest, or Import a pack you downloaded.'}</Empty>
          )}
          <div className="hub-grid">
            {items.map((a) => (
              <div key={a.id} className={`hub-card${sel.has(a.id) ? ' sel' : ''}`} onClick={(e) => (e.ctrlKey || e.metaKey || e.shiftKey ? toggle(a.id) : setOpen(a.id))}>
                <div className={`check${sel.has(a.id) ? ' on' : ''}`} onClick={(e) => { e.stopPropagation(); toggle(a.id) }} title="Select">{sel.has(a.id) && <Check size={12} />}</div>
                <span className="src" title={a.collection || undefined}>{a.collection && a.source === 'local' ? a.collection : sourceNames[a.source] ?? a.source}</span>
                <Thumb src={a.thumb_url} kind={a.kind} audio={audioUrl(a)} cover={a.kind === 'material' || a.kind === 'hdri'} />
                <div className="meta">
                  <div className="name" title={a.name}>{a.name}</div>
                  <Badges kind={a.kind} license={a.license} cls={a.license_class} rigged={a.rigged} animated={a.animated} clips={a.stats?.clips?.length} />
                  <div className="faint" style={{ fontSize: 10 }}>
                    {[a.formats.join('/').toUpperCase(), a.stats?.polycount ? `${Number(a.stats.polycount).toLocaleString()} tris` : '', a.stats?.duration ? `${Number(a.stats.duration).toFixed(1)}s` : '',
                      a.stats?.texture_size ? `${a.stats.texture_size}px` : '', a.total_bytes ? fmtSize(a.total_bytes) : ''].filter(Boolean).join(' · ')}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div ref={sentinel} className="hub-sentinel">{loading ? 'Loading…' : more ? '' : items.length ? `All ${total} shown` : ''}</div>
        </div>
      </div>
      {open && <AssetDetail id={open} sourceNames={sourceNames} onClose={() => setOpen(null)} onDeleted={() => { setOpen(null); load(1, f) }}
        onTag={(t) => { setOpen(null); setQInput(t) }} kindLabel={kindLabel} onChanged={() => load(1, f)} />}
    </div>
  )
}
