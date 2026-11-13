import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FolderPlus, Image as ImageIcon, MousePointerClick, Music, Pencil, Scissors, Trash2, Upload, Video, X } from 'lucide-react'
import { api, fmtSize, fmtTime, useJob, waitJob } from '../../api/client'
import type { Catalog, JobInfo, MediaRow } from '../../types/api'
import { emit, on } from '../../shared/bus'
import SegmentDialog from '../../shared/SegmentDialog'
import { Empty, JobBar } from '../../shared/ui'
import SearchPanel from '../ai/SearchPanel'
import ActivityHistory from '../../shared/projects/ActivityHistory'

const AssetHub = lazy(() => import('../hub/AssetHub'))
const AnimLibraryTab = lazy(() => import('./AnimLibraryTab'))
const ModelsLibraryTab = lazy(() => import('./ModelsLibraryTab'))

type Sub = 'history' | 'mine' | 'anim' | 'models' | 'hub' | 'builtin' | 'stock' | 'search'
const SUBS: readonly (readonly [Sub, string])[] = [['mine', 'My media'], ['anim', 'Animations'], ['models', '3D Models'], ['hub', 'Asset library'], ['builtin', 'Built-in'], ['stock', 'Stock'], ['search', 'Search this PC'], ['history', 'History']]
const KINDS = [{ id: '', label: 'All' }, { id: 'video', label: 'Video' }, { id: 'audio', label: 'Audio' }, { id: 'image', label: 'Image' }]
const ORIGINS = [{ id: '', label: 'All sources' }, { id: 'import', label: 'Local import' }, { id: 'upload', label: 'Upload' }, { id: 'download', label: 'Link download' },
  { id: 'music', label: 'AI music' }, { id: 'segment', label: 'Segments' }, { id: 'ai', label: 'AI processed' }, { id: 'export', label: 'Exported' }]

export default function Library() {
  const [sub, setSubState] = useState<Sub>(() => { try { const v = localStorage.getItem('cutlab.library.sub') as Sub; return SUBS.some(([id]) => id === v) ? v : 'mine' } catch { return 'mine' } })
  const setSub = (s: Sub) => { setSubState(s); try { localStorage.setItem('cutlab.library.sub', s) } catch { /* ignore */ } }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ padding: '10px 16px 0' }}>
        <div className="seg-tabs">
          {SUBS.map(([id, l]) =>
            <button key={id} className={sub === id ? 'on' : ''} onClick={() => setSub(id)}>{l}</button>)}
        </div>
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {sub === 'mine' && <Mine />}
        {sub === 'history' && <div style={{ padding: 16, flex: 1, display: 'flex', minHeight: 0, maxWidth: 1000 }}><ActivityHistory areas={['library', 'download', 'segment', 'ai', 'anim', 'video', 'other']} title="All activity" limit={150} /></div>}
        {sub === 'anim' && <Suspense fallback={<Empty>Loading…</Empty>}><AnimLibraryTab /></Suspense>}
        {sub === 'models' && <Suspense fallback={<Empty>Loading…</Empty>}><ModelsLibraryTab /></Suspense>}
        {sub === 'hub' && <Suspense fallback={<Empty>Loading…</Empty>}><AssetHub /></Suspense>}
        {sub === 'builtin' && <Builtin />}
        {sub === 'stock' && <Stock />}
        {sub === 'search' && <div style={{ padding: 16, flex: 1, overflow: 'auto', maxWidth: 900 }}>
          <SearchPanel onAddAsset={(m) => emit('cutlab:media-changed', { media: m })} /></div>}
      </div>
    </div>
  )
}

function KindIcon({ k }: { k: string }) { return k === 'video' ? <Video size={12} /> : k === 'audio' ? <Music size={12} /> : <ImageIcon size={12} /> }

function Mine() {
  const [items, setItems] = useState<MediaRow[]>([])
  const [kind, setKind] = useState('')
  const [origin, setOrigin] = useState('')
  const [cat, setCat] = useState('')
  const [cats, setCats] = useState<{ id: string; label: string; count: number }[]>([])
  const [catJob, setCatJob] = useState('')
  const [q, setQ] = useState('')
  const [path, setPath] = useState('')
  const [sel, setSel] = useState<MediaRow | null>(null)
  const [seg, setSeg] = useState<MediaRow | null>(null)
  const [drag, setDrag] = useState(false)
  const imp = useJob<{ items: MediaRow[]; errors: string[] }>()
  const file = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    const p = new URLSearchParams(); if (kind) p.set('kind', kind); if (origin) p.set('origin', origin); if (q) p.set('q', q); if (cat) p.set('tag', `cat:${cat}`)
    api.get<MediaRow[]>(`/api/media?${p}`).then(setItems).catch(() => {})
    api.get<{ categories: { id: string; label: string; count: number }[] }>('/api/intake/categories').then((r) => setCats(r.categories)).catch(() => {})
  }, [kind, origin, q, cat])
  useEffect(() => { load() }, [load])
  useEffect(() => on('cutlab:media-changed', load), [load])

  const doImport = async (paths: string[]) => {
    try { await imp.run(() => api.post<JobInfo>('/api/media/import', { paths })); setPath(''); load() } catch { /* JobBar */ }
  }
  const upload = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) { try { await api.upload('/api/media/upload', f) } catch (e: any) { alert(`${f.name}: ${e.message}`) } }
    load()
  }
  const del = async (m: MediaRow) => { if (confirm(`Remove "${m.name}" from the library? (The original file is not deleted)`)) { await api.del(`/api/media/${m.id}`); setSel(null); load() } }
  const rename = async (m: MediaRow) => { const n = prompt('Rename', m.name); if (n) { const r = await api.patch<MediaRow>(`/api/media/${m.id}`, { name: n }); setSel(r); load() } }
  const stats = useMemo(() => `${items.length} items`, [items])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) clamp(260px, 24vw, 340px)', gap: 12, padding: 16, flex: 1, minHeight: 0 }}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files) }}>
      <div className="panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, outline: drag ? '2px dashed var(--accent)' : undefined }}>
        <div className="row" style={{ padding: 10, borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
          <div className="seg-tabs">{KINDS.map((k) => <button key={k.id} className={kind === k.id ? 'on' : ''} onClick={() => setKind(k.id)}>{k.label}</button>)}</div>
          <select className="select" style={{ width: 120 }} value={origin} onChange={(e) => setOrigin(e.target.value)}>{ORIGINS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
          <select className="select" style={{ width: 140 }} value={cat} onChange={(e) => setCat(e.target.value)} title="Auto category (CLIP for images and videos, name and length for audio)">
            <option value="">All categories</option>
            {cats.map((c) => <option key={c.id} value={c.id}>{c.label} ({c.count})</option>)}
          </select>
          <button className="btn sm ghost" title="Auto-categorise everything that has no category yet" disabled={!!catJob} onClick={async () => {
            try { const j = await api.post<JobInfo>('/api/intake/categorize', { only_missing: true }); setCatJob('Categorising…'); await waitJob(j, (x) => setCatJob(`Categorising… ${Math.round(x.progress * 100)}%`)); load() } catch { /* ignore */ } finally { setCatJob('') }
          }}>{catJob || 'Auto-categorise'}</button>
          <input className="input" style={{ width: 180 }} placeholder="Search name/tag" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="faint">{stats}</span><span className="grow" />
          <input className="input" style={{ width: 260 }} placeholder="Paste a local folder or file path, e.g. D:\Media" value={path} onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && path.trim() && doImport(path.split('\n').map((s) => s.trim().replace(/^"|"$/g, '')).filter(Boolean))} />
          <button className="btn" disabled={!path.trim() || imp.busy} onClick={() => doImport([path.trim().replace(/^"|"$/g, '')])}><FolderPlus size={14} />Import path</button>
          <button className="btn" onClick={() => file.current?.click()}><Upload size={14} />Upload files</button>
          <input ref={file} type="file" multiple hidden accept="video/*,audio/*,image/*" onChange={(e) => e.target.files && upload(e.target.files)} />
        </div>
        <div style={{ padding: '0 10px' }}><JobBar job={imp.job} error={imp.error} onCancel={imp.cancel} /></div>
        <div style={{ padding: 10, overflow: 'auto', flex: 1 }}>
          {!items.length && <Empty>The library is empty — import a local folder, drop in files, or paste a video link in Link Download</Empty>}
          <div className="media-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}>
            {items.map((m) => (
              <div key={m.id} className="media-card" style={{ borderColor: sel?.id === m.id ? 'var(--accent)' : undefined }} onClick={() => setSel(m)}
                onDoubleClick={() => m.kind === 'image' ? emit('cutlab:open-image', { media: m }) : emit('cutlab:add-to-timeline', { media: m })}>
                <img src={m.thumb} loading="lazy" />
                {m.kind !== 'image' && <span className="dur">{fmtTime(m.duration)}</span>}
                <div className="cap row" style={{ gap: 4 }}><KindIcon k={m.kind} /><span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.name}</span></div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="panel" style={{ overflow: 'auto' }}>
        {!sel ? <Empty icon={MousePointerClick} title="No item selected">Click an item to see its details. Double-click sends video and audio to the timeline and opens images in the Image Editor.</Empty> : (
          <div style={{ padding: 12 }}>
            <div className="row"><b className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{sel.name}</b>
              <button className="btn ghost icon sm" onClick={() => setSel(null)}><X size={13} /></button></div>
            <div style={{ margin: '10px 0', background: '#000', borderRadius: 8, overflow: 'hidden' }}>
              {sel.kind === 'video' && <video key={sel.id} src={sel.url} controls style={{ width: '100%', display: 'block' }} />}
              {sel.kind === 'audio' && <div style={{ padding: 10 }}><img src={sel.thumb} style={{ width: '100%' }} /><audio key={sel.id} src={sel.url} controls style={{ width: '100%' }} /></div>}
              {sel.kind === 'image' && <img src={sel.url} style={{ width: '100%', display: 'block' }} />}
            </div>
            <div className="muted" style={{ fontSize: 12, lineHeight: 1.8 }}>
              <div>Type: {sel.kind}　Source: {sel.origin}</div>
              {(sel as any).meta?.classify?.category && <div>Category: {(sel as any).meta.classify.category}{(sel as any).meta.classify.subjects?.length ? ` · ${(sel as any).meta.classify.subjects.join(', ')}` : ''}{(sel as any).meta.classify.style ? ` · ${(sel as any).meta.classify.style}` : ''}</div>}
              {sel.width > 0 && <div>Size: {sel.width}×{sel.height}{sel.fps ? ` @ ${sel.fps}fps` : ''}</div>}
              {sel.duration > 0 && <div>Duration: {fmtTime(sel.duration)}　Size: {fmtSize(sel.size)}</div>}
              <div style={{ wordBreak: 'break-all' }} className="faint">{sel.path}</div>
              {sel.source_url && <div style={{ wordBreak: 'break-all' }} className="faint">{sel.source_url}</div>}
            </div>
            <div className="row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
              {sel.kind !== 'image' && <button className="btn sm primary" onClick={() => emit('cutlab:add-to-timeline', { media: sel })}>Add to timeline</button>}
              {sel.kind === 'image' && <>
                <button className="btn sm primary" onClick={() => emit('cutlab:open-image', { media: sel })}>Edit image</button>
                <button className="btn sm" onClick={() => emit('cutlab:add-to-timeline', { media: sel })}>Add to timeline</button></>}
              {sel.kind !== 'image' && <button className="btn sm" onClick={() => setSeg(sel)}><Scissors size={13} />Smart split</button>}
              <button className="btn sm" onClick={() => rename(sel)}><Pencil size={13} />Rename</button>
              <button className="btn sm danger" onClick={() => del(sel)}><Trash2 size={13} />Remove</button>
            </div>
          </div>
        )}
      </div>
      {seg && <SegmentDialog media={seg} onClose={() => setSeg(null)} />}
    </div>
  )
}

function Builtin() {
  const [cat, setCat] = useState<Catalog | null>(null)
  const [tab, setTab] = useState<'stickers' | 'backgrounds' | 'transitions' | 'filters' | 'fonts' | 'textPresets'>('stickers')
  const fontFile = useRef<HTMLInputElement>(null)
  useEffect(() => { api.get<Catalog>('/api/assets/catalog').then(setCat).catch(() => {}) }, [])
  const uploadFont = async (f: File) => { try { await api.upload('/api/assets/fonts/upload', f); setCat(await api.get('/api/assets/catalog')) } catch (e: any) { alert(e.message) } }
  if (!cat) return <Empty>Loading…</Empty>
  const tabs = [['stickers', 'Stickers'], ['backgrounds', 'Backgrounds'], ['textPresets', 'Text styles'], ['transitions', 'Transitions'], ['filters', 'Filters'], ['fonts', 'Fonts']] as const
  return (
    <div style={{ padding: 16, flex: 1, overflow: 'auto' }}>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="seg-tabs">{tabs.map(([id, l]) => <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{l}</button>)}</div>
        {tab === 'fonts' && <><button className="btn sm" onClick={() => fontFile.current?.click()}><Upload size={13} />Import font (.ttf/.otf)</button>
          <input ref={fontFile} hidden type="file" accept=".ttf,.otf" onChange={(e) => e.target.files?.[0] && uploadFont(e.target.files[0])} /></>}
      </div>
      {tab === 'stickers' && <div className="media-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))' }}>
        {cat.stickers.map((s) => <div key={s.id} className="media-card" style={{ background: 'repeating-conic-gradient(#222 0 25%, #2a2a2a 0 50%) 0 0/16px 16px' }}><img src={s.url} style={{ objectFit: 'contain', aspectRatio: '1' }} /><div className="cap">{s.name}</div></div>)}</div>}
      {tab === 'backgrounds' && <div className="media-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
        {cat.backgrounds.map((b) => <div key={b.id} className="media-card"><div style={{ aspectRatio: '16/9', background: b.css }} /><div className="cap">{b.name}</div></div>)}</div>}
      {tab === 'transitions' && <div className="row" style={{ flexWrap: 'wrap' }}>{cat.transitions.map((t) => <span key={t.id} className="chip" style={{ padding: '6px 14px', fontSize: 13 }}>{t.name} <span className="faint">{t.id}</span></span>)}</div>}
      {tab === 'filters' && <div className="row" style={{ flexWrap: 'wrap' }}>{cat.filters.map((f) => <span key={f.id} className="chip" style={{ padding: '6px 14px', fontSize: 13 }}>{f.name}</span>)}</div>}
      {tab === 'textPresets' && <div className="row" style={{ flexWrap: 'wrap', gap: 12 }}>{cat.textPresets.map((t) => (
        <div key={t.id} className="panel" style={{ padding: '14px 20px', background: '#222' }}>
          <span style={{ fontFamily: t.text.font, fontWeight: t.text.bold ? 700 : 400, color: t.text.color, fontSize: 22, background: t.text.bg || undefined, padding: t.text.bg ? '2px 8px' : 0,
            WebkitTextStroke: t.text.strokeWidth ? `1px ${t.text.stroke}` : undefined }}>{t.text.content}</span><div className="faint" style={{ fontSize: 11 }}>{t.name}</div></div>))}</div>}
      {tab === 'fonts' && <div style={{ columnWidth: 220 }}>{cat.fonts.map((f) => <div key={f.family} style={{ fontFamily: `'${f.family}'`, padding: '3px 0', fontSize: 15 }}>{f.family}{f.url && <span className="chip" style={{ marginLeft: 6 }}>Custom</span>}</div>)}</div>}
    </div>
  )
}

function Stock() {
  const [q, setQ] = useState(''); const [kind, setKind] = useState<'photo' | 'video'>('photo')
  const [items, setItems] = useState<any[]>([]); const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  const imp = useJob<{ media: MediaRow }>()
  const search = async () => { setBusy(true); setErr(''); try { setItems(await api.get(`/api/assets/stock?q=${encodeURIComponent(q)}&kind=${kind}`)) } catch (e: any) { setErr(e.message); setItems([]) } finally { setBusy(false) } }
  const add = async (it: any) => { try { const j = await imp.run(() => api.post('/api/assets/stock/import', { url: it.url, name: `${it.source} ${it.id}`, kind: it.kind })); j.result && emit('cutlab:media-changed', { media: j.result.media }) } catch { /* JobBar */ } }
  return (
    <div style={{ padding: 16, flex: 1, overflow: 'auto' }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <input className="input" style={{ width: 300 }} placeholder="Search royalty-free media (Pexels / Pixabay)" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
        <div className="seg-tabs"><button className={kind === 'photo' ? 'on' : ''} onClick={() => setKind('photo')}>Photos</button><button className={kind === 'video' ? 'on' : ''} onClick={() => setKind('video')}>Videos</button></div>
        <button className="btn primary" disabled={!q || busy} onClick={search}>Search</button>
      </div>
      {err && <div className="err">{err}</div>}
      <JobBar job={imp.job} error={imp.error} />
      <div className="media-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', marginTop: 10 }}>
        {items.map((it) => <div key={it.id} className="media-card" onClick={() => add(it)} title="Click to download to the library"><img src={it.thumb} referrerPolicy="no-referrer" /><div className="cap">{it.source} · {it.kind}</div></div>)}
      </div>
    </div>
  )
}
