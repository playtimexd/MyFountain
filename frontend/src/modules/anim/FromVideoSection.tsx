import { useEffect, useState } from 'react'
import { Clapperboard, Footprints } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'
import MediaPicker from '../ai/MediaPicker'
import type { MediaRow } from '../../types/api'
import type { AnimClip, AnimRig } from './animApi'
import { Note, Section } from './animUi'

interface Backend { id: string; name: string; available: boolean; note?: string; size?: string | number }

interface Props {
  rigs: AnimRig[]
  media: MediaRow | null
  setMedia: (m: MediaRow | null) => void
  range: { start?: number; end?: number }
  setRange: (r: { start?: number; end?: number }) => void
  reload: () => void
  onOpenClip: (clipId: string) => void
  onRetarget: (clipId: string) => void
}

export default function FromVideoSection({ rigs, media, setMedia, range, setRange, reload, onOpenClip, onRetarget }: Props) {
  const [subject, setSubject] = useState<'auto' | 'humanoid' | 'quadruped'>('auto')
  const [backend, setBackend] = useState('auto')
  const [rigId, setRigId] = useState('')
  const [backends, setBackends] = useState<Backend[] | null>(null)
  const [backendErr, setBackendErr] = useState<string | null>(null)
  const [clip, setClip] = useState<AnimClip | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const { job, error, run, cancel, busy } = useJob<any>()

  useEffect(() => {
    api.get<Backend[]>('/api/mocap/backends').then((b) => { setBackends(b); setBackendErr(null) })
      .catch((e: any) => { setBackends(null); setBackendErr(/404|Not Found/i.test(e.message) ? 'The motion-capture backend is not available in this engine build (/api/mocap not found).' : e.message) })
  }, [])

  const start = async () => {
    if (!media) return
    setErr(null); setClip(null)
    try {
      const d = await run(() => api.post('/api/mocap', { mediaId: media.id, start: range.start, end: range.end, subject, backend, rigId: rigId || undefined }))
      const r = d.result || {}
      const file: string | undefined = r.motion_file
      if (!file) throw new Error('The motion-capture job finished without a motion file.')
      const imp = await run(() => api.post('/api/anim/clips/import', { path: file, name: `${media.name} (motion)` }))
      const c = imp.result?.clip as AnimClip | undefined
      if (c) setClip(c)
      reload()
    } catch (e: any) {
      const m = e.message || String(e)
      setErr(/404|Not Found/i.test(m) ? 'Motion capture is not available: the engine has no /api/mocap router. Restart the engine after the mocap module was added.' : m)
    }
  }

  const avail = (backends || []).filter((b) => b.available)
  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 14, maxWidth: 820 }}>
      <Section title="Extract motion from a video">
        <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>Best results: a fixed camera, the whole body in frame, side or three-quarter view, a plain background and 3–5 clean steps. A short range of the video is enough.</div>
        <MediaPicker value={media?.id || null} onChange={(m) => { setMedia(m); setRange({}) }} kinds={['video']} label="Reference video (from the library)" extra={media ? [media] : []} />
        <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
          <div className="field" style={{ width: 110 }}><label>Start (s)</label><input className="input" type="number" min={0} step={0.1} value={range.start ?? ''} placeholder="0" onChange={(e) => setRange({ ...range, start: e.target.value === '' ? undefined : +e.target.value })} /></div>
          <div className="field" style={{ width: 110 }}><label>End (s)</label><input className="input" type="number" min={0} step={0.1} value={range.end ?? ''} placeholder={media ? media.duration.toFixed(1) : 'end'} onChange={(e) => setRange({ ...range, end: e.target.value === '' ? undefined : +e.target.value })} /></div>
          <div className="field" style={{ width: 160 }}><label>Subject</label>
            <select className="select" value={subject} onChange={(e) => setSubject(e.target.value as typeof subject)}>
              <option value="auto">Auto-detect</option><option value="humanoid">Humanoid (person)</option><option value="quadruped">Quadruped (animal)</option>
            </select></div>
          <div className="field" style={{ width: 200 }}><label>Method</label>
            <select className="select" value={backend} onChange={(e) => setBackend(e.target.value)}>
              <option value="auto">Auto (best available)</option>
              {(backends || []).map((b) => <option key={b.id} value={b.id}>{b.name}{b.available ? '' : ' (not installed)'}</option>)}
            </select></div>
          <div className="field grow" style={{ minWidth: 160 }}><label>Target character (optional, improves proportions)</label>
            <select className="select" value={rigId} onChange={(e) => setRigId(e.target.value)}>
              <option value="">None</option>{rigs.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select></div>
        </div>
        {backendErr && <Note tone="warn">{backendErr}</Note>}
        {backends && !avail.length && (
          <Note tone="warn">No motion-capture model is installed yet. {backends.map((b) => `${b.name}: ${b.note || 'not installed'}`).join(' · ')} Model weights are only downloaded when you ask for it in Settings / the backend's Prepare button.</Note>
        )}
        {backends && avail.length > 0 && <div className="faint" style={{ fontSize: 11 }}>Available: {avail.map((b) => b.name).join(', ')}</div>}
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={!media || busy} onClick={start}><Footprints size={14} />Extract motion</button>
          {media && <span className="faint" style={{ fontSize: 11 }}>{media.name}{range.start != null || range.end != null ? ` · ${range.start ?? 0}s → ${range.end ?? media.duration.toFixed(1)}s` : ''}</span>}
        </div>
        <JobBar job={job} error={error} onCancel={cancel} />
        {err && !error && <Note tone="err">{err}</Note>}
        {clip && (
          <Note tone="ok">
            Motion extracted: <b>{clip.name}</b> · {clip.frames} frames @ {clip.fps.toFixed(0)} fps ({clip.skeleton}).
            <span className="row" style={{ marginTop: 6, gap: 6 }}>
              <button className="btn sm primary" onClick={() => onRetarget(clip.id)}>Retarget onto a character</button>
              <button className="btn sm" onClick={() => onOpenClip(clip.id)}><Clapperboard size={12} />Open in Library</button>
            </span>
          </Note>
        )}
      </Section>
      <Note>Need a reference clip? Use <b>Generate → Reference video</b> to create one, or download/segment a video in the Library and send it here with “Extract motion”.</Note>
    </div>
  )
}
