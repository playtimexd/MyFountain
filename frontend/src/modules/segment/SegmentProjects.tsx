// Video Segment <-> project store. A segment project = the source video, the marked ranges (with labels and
// on/off), the detection settings and the clips produced by the last split.
import { useEffect, useState } from 'react'
import { SplitSquareHorizontal } from 'lucide-react'
import { api } from '../../api/client'
import type { MediaRow } from '../../types/api'
import WorkspaceProjects, { workspaceSerializer } from '../../shared/projects/WorkspaceProjects'
import { NewProjectForm } from '../../shared/projects/ProjectHome'
import { createProjectSession, type ProjectSession } from '../../shared/projects/session'

interface Range { id: number; start: number; end: number; label: string; on: boolean }
export interface SegmentWorkspace { media: MediaRow | null; ranges: Range[]; method: string; every: number; accurate: boolean; results: MediaRow[] }

export const segmentSession: ProjectSession = createProjectSession({ kind: 'segment', delay: 1000, serialize: workspaceSerializer(() => segmentSession) })

const METHODS = [
  { id: 'scene', label: 'Scene cuts', detail: 'Detect shot changes' },
  { id: 'silence', label: 'Speech / silence', detail: 'Cut at pauses' },
  { id: 'asr', label: 'Sentences', detail: 'Whisper transcript' },
  { id: 'fixed', label: 'Every N seconds', detail: 'Fixed length' },
]
const isMedia = (m: any): m is MediaRow => !!m && typeof m === 'object' && typeof m.id === 'string'

const parse = (raw: any): SegmentWorkspace => ({
  media: isMedia(raw?.media) ? raw.media : null,
  // fresh ids far above the editor's own counter so later-added ranges never collide
  ranges: (Array.isArray(raw?.ranges) ? raw.ranges : []).filter((r: any) => r && Number.isFinite(r.start) && Number.isFinite(r.end))
    .map((r: any, i: number) => ({ id: 1_000_000 + i, start: +r.start, end: +r.end, label: String(r.label ?? ''), on: r.on !== false })),
  method: METHODS.some((m) => m.id === raw?.method) ? raw.method : 'scene',
  every: Number.isFinite(raw?.every) ? raw.every : 10,
  accurate: raw?.accurate !== false,
  results: (Array.isArray(raw?.results) ? raw.results : []).filter(isMedia),
})
const summary = (s: SegmentWorkspace) => ({
  video: s.media?.name ?? null, duration: s.media?.duration ?? null, ranges: s.ranges.length, clips: s.results.length, thumb_url: s.media?.thumb ?? null,
})

function NewSegment({ create }: { create: (name: string, init: SegmentWorkspace) => Promise<void> }) {
  const [name, setName] = useState('')
  const [method, setMethod] = useState('scene')
  const [videos, setVideos] = useState<MediaRow[]>([])
  const [vid, setVid] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.get<MediaRow[]>('/api/media?kind=video&limit=200').then(setVideos).catch(() => undefined) }, [])
  const go = async () => {
    setBusy(true); setErr(null)
    const media = videos.find((v) => v.id === vid) || null
    try { await create(name.trim() || media?.name || 'Untitled split', { media, ranges: [], method, every: 10, accurate: true, results: [] }); setName('') }
    catch (e: any) { setErr(e.message || String(e)) } finally { setBusy(false) }
  }
  return (
    <NewProjectForm name={name} setName={setName} presets={METHODS} preset={method} setPreset={setMethod} onCreate={go} busy={busy} error={err}>
      <div className="field"><label>Source video</label>
        <select className="input" value={vid} onChange={(e) => setVid(e.target.value)}>
          <option value="">Choose later (or download from a link)</option>
          {videos.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      </div>
      <div className="faint" style={{ fontSize: 11, marginBottom: 8 }}>The preset is the automatic detection method; you can still mark ranges by hand.</div>
    </NewProjectForm>
  )
}

/** Mounted once in Video Segment's source bar. */
export default function SegmentProjects({ state, set }: {
  state: SegmentWorkspace
  set: { setMedia: (m: MediaRow | null) => void; setRanges: (r: Range[]) => void; setMethod: (m: any) => void; setEvery: (n: number) => void; setAccurate: (b: boolean) => void; setResults: (r: MediaRow[]) => void; reset: () => void }
}) {
  return (
    <WorkspaceProjects<SegmentWorkspace> session={segmentSession} title="Video Segment · Projects" icon={SplitSquareHorizontal} layout="inline"
      state={state} parse={parse} summary={summary}
      apply={(s) => { set.reset(); set.setMedia(s.media); set.setRanges(s.ranges); set.setMethod(s.method); set.setEvery(s.every); set.setAccurate(s.accurate); set.setResults(s.results) }}
      describe={(p) => [p.meta?.video, typeof p.meta?.ranges === 'number' ? `${p.meta.ranges} ranges` : '', p.meta?.clips ? `${p.meta.clips} clips` : ''].filter(Boolean).join(' · ')}
      newPanel={(create) => <NewSegment create={create} />} />
  )
}
