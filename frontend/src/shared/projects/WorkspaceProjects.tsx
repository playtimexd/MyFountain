// Projects for "workspace" areas whose state is a small JSON object held in React state (3D Models, Video Segment …):
// the project document IS that object. Mount once inside the area: it renders the Projects button + save status
// (`layout` nav = stacked block for a left nav, inline = a toolbar row) and the start-screen overlay.
import { useEffect, useRef, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import ProjectHome, { ProjectsButton, SaveChip } from './ProjectHome'
import { projectsApi, type ProjectMeta } from './api'
import type { ProjectSession } from './session'

/** The latest workspace of each session, read by its serializer (set on every render of WorkspaceProjects). */
const live = new Map<ProjectSession, { state: unknown; summary?: (s: any) => Record<string, unknown> }>()

/** Serializer to pass to createProjectSession for a workspace session. */
export const workspaceSerializer = (getSession: () => ProjectSession) => () => {
  const l = live.get(getSession())
  return l ? { body: JSON.stringify({ version: 1, ...(l.state as object) }), meta: l.summary?.(l.state) ?? {} } : null
}

export default function WorkspaceProjects<T extends object>({ session, title, icon, state, parse, apply, summary, describe, newPanel, layout = 'nav' }: {
  session: ProjectSession
  title: string
  icon: LucideIcon
  state: T
  parse: (raw: any) => T
  apply: (s: T) => void
  summary?: (s: T) => Record<string, unknown>
  describe?: (p: ProjectMeta) => string
  /** The "New project" form; call `create(name, initialState)` from it. */
  newPanel: (create: (name: string, init: T) => Promise<void>) => ReactNode
  layout?: 'nav' | 'inline'
}) {
  const home = session.use((s) => s.home)
  const id = session.use((s) => s.id)
  const name = session.use((s) => s.name)
  const lastJson = useRef('')
  const ready = useRef(false)
  live.set(session, { state, summary: summary as any })

  const load = (s: T) => { lastJson.current = JSON.stringify(s); apply(s) }

  // reopen the last project after a reload
  useEffect(() => {
    const cur = session.use.getState().id
    if (!cur) { ready.current = true; return }
    projectsApi.doc(cur).then((raw) => load(parse(raw)))
      .catch((e: Error) => { if (/no saved document|404|not found/i.test(e.message)) { session.setCurrent(null); session.showHome(true) } })
      .finally(() => { ready.current = true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // autosave on any change of the workspace
  const json = JSON.stringify(state)
  useEffect(() => {
    if (!ready.current || !id || json === lastJson.current) return
    lastJson.current = json
    session.schedule()
  }, [json, id, session])

  const open = async (p: ProjectMeta) => {
    const s = parse(await projectsApi.doc(p.id))
    session.setCurrent(p.id, p.name)
    load(s)
  }
  const create = async (nm: string, init: T) => {
    await session.flush()
    live.set(session, { state: init, summary: summary as any })
    const p = await projectsApi.create(session.kind, nm, { version: 1, ...init }, summary?.(init) ?? {})
    session.setCurrent(p.id, p.name)
    load(init)
  }

  return (
    <>
      {layout === 'nav' ? (
        <div style={{ borderTop: '1px solid var(--line)', paddingTop: 8, marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div className="faint" style={{ fontSize: 11 }}>Project</div>
          <div style={{ fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>{id ? name : 'No project open'}</div>
          <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}><ProjectsButton session={session} /><SaveChip session={session} /></div>
        </div>
      ) : (
        <div className="row" style={{ gap: 8, minWidth: 0 }}>
          <ProjectsButton session={session} />
          <span style={{ fontWeight: 600, fontSize: 12, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>{id ? name : 'No project open'}</span>
          <SaveChip session={session} />
        </div>
      )}
      {home && <ProjectHome kind={session.kind} title={title} icon={icon} session={session} onOpen={open} describe={describe} newPanel={newPanel(create)} />}
    </>
  )
}
