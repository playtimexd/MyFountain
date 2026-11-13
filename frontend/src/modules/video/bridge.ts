// Live bridge client: lets external drivers (CutLab MCP server -> Claude Code / Codex) read and edit the open timeline.
// Backend: backend/app/routers/bridge.py. Only the visible editor tab pushes snapshots and consumes commands.
import { create } from 'zustand'
import { useProject } from './store'
import { player } from './engine/player'
import { clock } from './engine/clock'
import { normalizeProject } from './ops'
import type { Dropped } from './opsSanitize'

interface BridgeState {
  lastCmd: number       // ms timestamp of the last command received
  pulse: number         // ms timestamp of the last command applied (for a short pulse animation)
  lastInfo: string      // human summary of the last command
  online: boolean       // engine bridge reachable
}
export const useBridge = create<BridgeState>(() => ({ lastCmd: 0, pulse: 0, lastInfo: '', online: false }))

const CLIENT = `ed-${Math.random().toString(36).slice(2, 10)}`

interface Command { id: string; type: string; payload: Record<string, any> }

async function post(url: string, body?: unknown, signal?: AbortSignal) {
  const r = await fetch(url, { method: 'POST', headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined, body: body === undefined ? undefined : JSON.stringify(body), signal })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

const snapshot = () => {
  const s = useProject.getState()
  const mediaIds: string[] = []
  for (const t of s.project.tracks) for (const c of t.clips) if (s.selection.includes(c.id) && c.assetId) mediaIds.push(c.assetId)
  return { project: s.project, selection: { clipIds: s.selection, mediaIds }, playhead: clock.time, client: CLIENT }
}

async function execute(cmd: Command): Promise<{ ok: boolean; error?: string; info?: Record<string, unknown> }> {
  const st = useProject.getState()
  const p = cmd.payload || {}
  switch (cmd.type) {
    case 'ops': {
      if (!Array.isArray(p.ops)) return { ok: false, error: 'payload.ops must be an array' }
      const r = await st.applyOpsReport(p.ops)
      const dropped = r.dropped.map((d: Dropped) => ({ op: d.op, reason: d.reason }))
      const info = { applied: r.applied, dropped, created: r.created }
      if (p.ops.length && !r.applied) return { ok: false, error: `all ${p.ops.length} op(s) were dropped: ${dropped.map((d) => d.reason).join('; ')}`, info }
      return { ok: true, info }
    }
    case 'seek': {
      const t = typeof p.t === 'string' ? parseFloat(p.t) : p.t
      if (typeof t !== 'number' || !Number.isFinite(t)) return { ok: false, error: 't must be a number (seconds)' }
      player.pause(); player.seek(Math.max(0, t))
      return { ok: true, info: { playhead: clock.time } }
    }
    case 'set_project': {
      const proj = normalizeProject(p.project)
      if (!proj) return { ok: false, error: 'payload.project is not a valid project (needs tracks[])' }
      player.pause()
      st.mutate(() => proj)            // undoable, like opening a project file but kept in history
      st.select([])
      if (clock.time > player.duration) player.seek(0)
      return { ok: true, info: { tracks: proj.tracks.length } }
    }
    case 'select': {
      const ids: string[] = Array.isArray(p.clipIds) ? p.clipIds.map(String) : []
      const all = new Set(st.project.tracks.flatMap((t) => t.clips.map((c) => c.id)))
      const ok = ids.filter((i) => all.has(i))
      st.select(ok)
      return { ok: true, info: { selected: ok.length, unknown: ids.filter((i) => !all.has(i)) } }
    }
  }
  return { ok: false, error: `unknown command type ${cmd.type}` }
}

/** Start the bridge loops. `isActive` = this editor is the one the user is looking at. Returns a stop function. */
export function startBridge(isActive: () => boolean): () => void {
  let stopped = false
  const ctrl = new AbortController()
  const active = () => !stopped && document.visibilityState === 'visible' && isActive()

  // ---- snapshot push (debounced)
  let pushTimer: ReturnType<typeof setTimeout> | undefined
  const push = () => {
    clearTimeout(pushTimer)
    pushTimer = setTimeout(() => {
      if (!active()) return
      post('/api/bridge/project', snapshot(), ctrl.signal).then(() => useBridge.setState({ online: true })).catch(() => useBridge.setState({ online: false }))
    }, 300)
  }
  const unsub = useProject.subscribe((s, prev) => { if (s.project !== prev.project || s.selection !== prev.selection || s.playhead !== prev.playhead) push() })
  const onVis = () => { if (document.visibilityState === 'visible') push() }
  document.addEventListener('visibilitychange', onVis)
  push()

  // ---- heartbeat
  const hb = setInterval(() => {
    if (!active()) return
    post(`/api/bridge/heartbeat?client=${CLIENT}`, undefined, ctrl.signal).then(() => useBridge.setState({ online: true })).catch(() => useBridge.setState({ online: false }))
  }, 2000)

  // ---- command long-poll loop
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
  void (async () => {
    let backoff = 1000
    let wasActive = false
    while (!stopped) {
      if (!active()) { wasActive = false; await sleep(800); continue }
      if (!wasActive) { wasActive = true; push() }
      try {
        const r = await fetch(`/api/bridge/pending?client=${CLIENT}&wait=15`, { signal: ctrl.signal })
        if (!r.ok) throw new Error(`${r.status}`)
        const { commands } = (await r.json()) as { commands: Command[] }
        backoff = 1000
        useBridge.setState({ online: true })
        for (const cmd of commands || []) {
          useBridge.setState({ lastCmd: Date.now() })
          let res: { ok: boolean; error?: string; info?: Record<string, unknown> }
          try { res = await execute(cmd) } catch (e: any) { res = { ok: false, error: e?.message || String(e) } }
          useBridge.setState({
            pulse: res.ok ? Date.now() : useBridge.getState().pulse,
            lastInfo: res.ok ? `${cmd.type} applied${res.info && 'applied' in res.info ? ` (${res.info.applied} op(s))` : ''}` : `${cmd.type} failed: ${res.error}`,
          })
          await post('/api/bridge/ack', { id: cmd.id, ok: res.ok, error: res.error ?? null, project: useProject.getState().project, info: res.info ?? null }, ctrl.signal).catch(() => {})
        }
        if (commands?.length) push()
      } catch {
        if (stopped) break
        useBridge.setState({ online: false })
        await sleep(backoff)
        backoff = Math.min(15000, backoff * 2)
      }
    }
  })()

  return () => {
    stopped = true
    ctrl.abort()
    clearTimeout(pushTimer)
    clearInterval(hb)
    unsub()
    document.removeEventListener('visibilitychange', onVis)
  }
}
