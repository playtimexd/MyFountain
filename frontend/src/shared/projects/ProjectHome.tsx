// Start screen for an editor area: "New project" (area-specific form) + "Recent projects" grid
// (search, sort, rename, duplicate, delete with confirmation). Rendered as an overlay over the editor.
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ArrowLeft, Copy, FolderClock, Pencil, Plus, Search, Trash2, X, type LucideIcon } from 'lucide-react'
import { projectsApi, timeAgo, type ProjectKind, type ProjectMeta } from './api'
import type { ProjectSession } from './session'
import './projects.css'

export interface Preset { id: string; label: string; detail?: string }

export function ConfirmDialog({ title, children, confirm = 'Delete', danger = true, onOk, onClose }: {
  title: string; children: ReactNode; confirm?: string; danger?: boolean; onOk: () => void; onClose: () => void
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: 'min(440px, 94vw)' }} role="dialog" aria-label={title}>
        <header><span className="grow">{title}</span><button className="btn ghost icon sm" onClick={onClose} title="Close"><X size={14} /></button></header>
        <div className="body">{children}</div>
        <div className="row" style={{ justifyContent: 'flex-end', padding: '0 16px 14px' }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className={`btn ${danger ? 'danger' : 'primary'}`} onClick={() => { onOk(); onClose() }} autoFocus>{confirm}</button>
        </div>
      </div>
    </div>
  )
}

/** Name + preset tiles + extra fields + Create button. */
export function NewProjectForm({ name, setName, presets, preset, setPreset, children, onCreate, busy, error, createLabel = 'Create project' }: {
  name: string; setName: (v: string) => void
  presets?: Preset[]; preset?: string; setPreset?: (id: string) => void
  children?: ReactNode
  onCreate: () => void; busy?: boolean; error?: string | null; createLabel?: string
}) {
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!busy) onCreate() }}>
      <div className="field"><label>Project name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Untitled project" maxLength={200} />
      </div>
      {presets && presets.length > 0 && (
        <div className="field"><label>Preset</label>
          <div className="ph-presets">
            {presets.map((p) => (
              <button type="button" key={p.id} className={`ph-preset ${preset === p.id ? 'on' : ''}`} onClick={() => setPreset?.(p.id)} title={p.detail ? `${p.label} · ${p.detail}` : p.label}>
                <b>{p.label}</b>{p.detail && <span>{p.detail}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
      {children}
      <button type="submit" className="btn primary" style={{ width: '100%', justifyContent: 'center', marginTop: 6 }} disabled={busy}><Plus size={14} />{busy ? 'Creating…' : createLabel}</button>
      {error && <div className="err">{error}</div>}
    </form>
  )
}

export function SaveChip({ session }: { session: ProjectSession }) {
  const s = session.use()
  if (!s.id) return <span className="ph-chip">Not saved to a project</span>
  if (s.status === 'error') return <span className="ph-chip err" title={s.error || ''}><i />{s.error?.startsWith('This project was deleted') ? 'Project deleted' : 'Autosave failed'}</span>
  if (s.status === 'saving') return <span className="ph-chip"><i />Saving…</span>
  if (s.status === 'pending') return <span className="ph-chip pending" title="Changes are saved automatically"><i />Unsaved changes</span>
  if (s.status === 'saved') return <span className="ph-chip" title={`Saved to the project store at ${new Date(s.savedAt).toLocaleTimeString('en-US')}`}><i style={{ background: 'var(--ok)' }} />Saved</span>
  return <span className="ph-chip"><i style={{ background: 'var(--ok)' }} />Autosave on</span>
}

export function ProjectsButton({ session, compact }: { session: ProjectSession; compact?: boolean }) {
  return (
    <button className="btn sm" title="Projects: create a new project or reopen a recent one" onClick={() => { void session.flush(true); session.showHome(true) }}>
      <FolderClock size={14} />{compact ? null : 'Projects'}
    </button>
  )
}

export default function ProjectHome({ kind, title, icon: Icon, session, onOpen, describe, newPanel, refreshKey = 0, onDeleted, onRenamed }: {
  kind: ProjectKind
  title: string
  icon: LucideIcon
  session: ProjectSession
  /** Load the project into the editor (the home screen closes once it resolves). */
  onOpen: (p: ProjectMeta) => Promise<void> | void
  describe?: (p: ProjectMeta) => string
  newPanel: ReactNode
  refreshKey?: number
  onDeleted?: (id: string) => void
  /** A project was renamed here (update the open document's own name if it is the current one). */
  onRenamed?: (id: string, name: string) => void
}) {
  const cur = session.use((s) => s.id)
  const [items, setItems] = useState<ProjectMeta[] | null>(null)
  const [q, setQ] = useState('')
  const [sort, setSort] = useState(() => { try { return localStorage.getItem('cutlab.projects.sort') || 'opened' } catch { return 'opened' } })
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null)
  const [confirmDel, setConfirmDel] = useState<ProjectMeta | null>(null)

  const load = useCallback(async () => {
    try { setItems(await projectsApi.list(kind, q.trim(), sort)); setErr(null) } catch (e: any) { setErr(e.message || String(e)); setItems((x) => x ?? []) }
  }, [kind, q, sort])
  useEffect(() => { const t = setTimeout(load, q ? 200 : 0); return () => clearTimeout(t) }, [load, q, refreshKey])
  useEffect(() => { try { localStorage.setItem('cutlab.projects.sort', sort) } catch { /* ignore */ } }, [sort])
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape' && cur && !confirmDel && !renaming && !document.querySelector('.modal-back')) session.showHome(false) }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [cur, confirmDel, renaming, session])

  const open = async (p: ProjectMeta) => {
    if (busy) return
    setBusy(p.id); setErr(null)
    try {
      if (cur && cur !== p.id) await session.flush(true)
      await onOpen(p)
      void projectsApi.open(p.id).catch(() => undefined)
      session.showHome(false)
    } catch (e: any) { setErr(`Could not open "${p.name}": ${e.message || e}`) } finally { setBusy(null) }
  }
  const rename = async () => {
    if (!renaming) return
    const name = renaming.name.trim()
    setRenaming(null)
    if (!name) return
    try {
      await projectsApi.patch(renaming.id, { name })
      if (renaming.id === cur) session.use.setState({ name })
      onRenamed?.(renaming.id, name)
      void load()
    } catch (e: any) { setErr(e.message) }
  }
  const dup = async (p: ProjectMeta) => {
    try { if (p.id === cur) await session.flush(true); await projectsApi.duplicate(p.id); void load() } catch (e: any) { setErr(e.message) }
  }
  const del = async (p: ProjectMeta) => {
    try {
      await projectsApi.remove(p.id)
      if (p.id === cur) session.setCurrent(null)
      onDeleted?.(p.id)
      void load()
    } catch (e: any) { setErr(e.message) }
  }

  return (
    <div className="ph-root" data-projects-home={kind}>
      <div className="ph-head">
        <Icon size={18} />
        <h2>{title}</h2>
        <span className="faint" style={{ fontSize: 12 }}>Projects are saved automatically while you work.</span>
        <div className="grow" />
        {cur && <button className="btn" onClick={() => session.showHome(false)} title="Back to the open project (Esc)"><ArrowLeft size={14} />Back to editor</button>}
      </div>
      <div className="ph-body">
        <div className="ph-new">
          <h3>New project</h3>
          {newPanel}
        </div>
        <div className="ph-recent">
          <div className="ph-tools">
            <h3 style={{ margin: 0 }}>Recent projects</h3>
            <span className="faint" style={{ fontSize: 12 }}>{items ? `${items.length}` : ''}</span>
            <div className="grow" />
            <div className="row" style={{ gap: 4, position: 'relative' }}>
              <Search size={14} className="faint" style={{ position: 'absolute', left: 8 }} />
              <input className="input" style={{ paddingLeft: 26 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search projects" aria-label="Search projects" />
            </div>
            <select className="select input" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort projects" style={{ width: 'auto' }}>
              <option value="opened">Last opened</option>
              <option value="updated">Last edited</option>
              <option value="created">Date created</option>
              <option value="name">Name</option>
            </select>
          </div>
          {err && <div className="err" style={{ marginBottom: 10 }}>{err}</div>}
          {items && !items.length && (
            <div className="ph-empty">{q ? `No projects match "${q}".` : 'No projects yet. Create one on the left — it is saved automatically and will show up here.'}</div>
          )}
          <div className="ph-grid">
            {items?.map((p) => (
              <div key={p.id} className={`ph-card ${p.id === cur ? 'cur' : ''}`} onClick={() => renaming?.id !== p.id && void open(p)} tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) void open(p) }} title={`Open "${p.name}"`}>
                <div className="ph-thumb">
                  {p.thumb_url ? <img src={p.thumb_url} alt="" loading="lazy" draggable={false} /> : <Icon size={28} />}
                </div>
                {p.id === cur && <span className="ph-badge">Open</span>}
                {busy === p.id && <span className="ph-badge" style={{ background: 'var(--bg-3)' }}>Opening…</span>}
                <div className="ph-acts" onClick={(e) => e.stopPropagation()}>
                  <button title="Rename" aria-label="Rename" onClick={() => setRenaming({ id: p.id, name: p.name })}><Pencil size={13} /></button>
                  <button title="Duplicate" aria-label="Duplicate" onClick={() => void dup(p)}><Copy size={13} /></button>
                  <button className="del" title="Delete" aria-label="Delete" onClick={() => setConfirmDel(p)}><Trash2 size={13} /></button>
                </div>
                <div className="ph-info">
                  {renaming?.id === p.id ? (
                    <input className="input" autoFocus value={renaming.name} onClick={(e) => e.stopPropagation()}
                      onChange={(e) => setRenaming({ id: p.id, name: e.target.value })} onBlur={() => void rename()}
                      onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Enter') void rename(); if (e.key === 'Escape') setRenaming(null) }} aria-label="New name" />
                  ) : <div className="ph-name">{p.name}</div>}
                  <div className="ph-sub">Edited {timeAgo(p.updated)}{describe ? ` · ${describe(p)}` : ''}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      {confirmDel && (
        <ConfirmDialog title="Delete project" onOk={() => void del(confirmDel)} onClose={() => setConfirmDel(null)}>
          Delete <b>{confirmDel.name}</b>{confirmDel.id === cur ? ' (currently open)' : ''}? It disappears from Recent projects; its files are moved to
          <span className="faint"> data/projects/_trash</span>. Media in the library is not touched.
        </ConfirmDialog>
      )}
    </div>
  )
}
