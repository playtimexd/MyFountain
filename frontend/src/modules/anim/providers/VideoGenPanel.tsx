import { useMemo, useState } from 'react'
import { Clapperboard, Eye, Plus } from 'lucide-react'
import { api, useJob } from '../../../api/client'
import { Field, JobBar } from '../../../shared/ui'
import type { MediaRow } from '../../../types/api'
import { CostConfirm, ImagePicker, Note, ProviderStatus, fmtEstimate, useEstimate, useProviders } from './providerUi'

export interface ProviderPanelProps { onAddMedia?: (m: MediaRow) => void; onAddRig?: (rig: { id: string; name: string }) => void }

const EXAMPLES = [
  'A brown bear walking, side view, static camera',
  'A wolf trotting across a snowy field, side view, static camera',
  'A horse walking at a steady pace, side view, static camera',
  'A domestic cat walking, three-quarter view, static camera',
  'A deer walking through a meadow, side view, static camera',
]
const ASPECTS = ['16:9', '9:16', '1:1', '4:3', '3:4', '21:9']

export default function VideoGenPanel({ onAddMedia }: ProviderPanelProps) {
  const { items: providers, loading, err, reload } = useProviders('/api/videogen/providers')
  const [provider, setProvider] = useState('auto')
  const [prompt, setPrompt] = useState(EXAMPLES[0])
  const [capture, setCapture] = useState(true)
  const [image, setImage] = useState<MediaRow | null>(null)
  const [duration, setDuration] = useState(5)
  const [aspect, setAspect] = useState('16:9')
  const [resolution, setResolution] = useState('480p')
  const [confirm, setConfirm] = useState(false)
  const [showReq, setShowReq] = useState(false)
  const [result, setResult] = useState<{ media: MediaRow; provider: string } | null>(null)
  const { job, error, run, cancel, busy } = useJob<{ media: MediaRow; provider: string }>()

  // mirror the server's "auto" rule: a ready local workflow first, then Seedance when a key is set
  const resolved = useMemo(() => {
    if (provider !== 'auto') return providers.find((p) => p.id === provider)
    return providers.find((p) => p.id === 'local' && p.available) || providers.find((p) => p.id === 'seedance' && p.available) || providers.find((p) => p.id === 'local')
  }, [provider, providers])
  const cloud = !!resolved?.cloud
  const cap = Math.max(2, Math.min(Number(resolved?.capabilities?.max_duration ?? 6), 10))
  const dur = Math.min(duration, cap)
  const body = { prompt: prompt.trim(), duration: dur, aspect, provider, capture_friendly: capture, image_mediaId: image?.id ?? null, resolution: resolved?.id === 'seedance' ? resolution : null }
  const { est, req, problem } = useEstimate('/api/videogen', body, prompt.trim().length > 0)
  const canGo = !busy && prompt.trim().length > 0 && !!resolved?.available && !problem && (!cloud || confirm)

  const generate = async () => {
    setResult(null)
    try {
      const done = await run(() => api.post('/api/videogen', { ...body, confirm_cost: cloud && confirm }))
      if (done.result?.media) setResult(done.result)
    } catch { /* shown by JobBar */ }
  }

  return (
    <div style={{ padding: 12, overflow: 'auto', height: '100%' }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <Clapperboard size={16} /><b>Reference video generation</b>
        <span className="faint" style={{ fontSize: 11 }}>source clips for motion capture</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        <div style={{ flex: '1 1 340px', minWidth: 0 }}>
          <Field label="Provider">
            <select className="select" value={provider} onChange={(e) => { setProvider(e.target.value); setConfirm(false) }}>
              <option value="auto">Auto (local if ready, else Seedance)</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.available ? '' : p.needs_key && !p.key_set ? ' - needs API key' : ' - unavailable'}
                </option>
              ))}
            </select>
          </Field>
          {err && <Note tone="err">Could not reach the engine: {err}. Restart the engine if the videogen router was just added.</Note>}
          {resolved && <ProviderStatus p={resolved} reload={reload} loading={loading} />}

          <Field label="Prompt">
            <textarea className="textarea" rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the subject and its movement" />
          </Field>
          <div className="row" style={{ flexWrap: 'wrap', gap: 4, margin: '-4px 0 8px' }}>
            {EXAMPLES.map((x) => <button key={x} className="chip" style={{ cursor: 'pointer', border: 0 }} onClick={() => setPrompt(x)} title={x}>{x.split(',')[0]}</button>)}
          </div>
          <label className="row" style={{ alignItems: 'flex-start', cursor: 'pointer', fontSize: 12, marginBottom: 10 }}>
            <input type="checkbox" checked={capture} onChange={(e) => setCapture(e.target.checked)} style={{ marginTop: 2 }} />
            <span><b>Capture-friendly</b> <span className="muted">appends: static camera, full body in frame, side / three-quarter view, plain background, seamless walk cycle (best for motion extraction)</span></span>
          </label>

          <ImagePicker label="Start image (optional, image-to-video)" value={image?.id ?? null} onChange={setImage} />

          <div className="row" style={{ alignItems: 'flex-start' }}>
            <div className="field grow"><label>Duration: {dur} s <span className="faint">(max {cap})</span></label>
              <input type="range" min={2} max={cap} step={1} value={dur} onChange={(e) => setDuration(+e.target.value)} /></div>
            <div className="field" style={{ width: 90 }}><label>Aspect</label>
              <select className="select" value={aspect} onChange={(e) => setAspect(e.target.value)}>{ASPECTS.map((a) => <option key={a}>{a}</option>)}</select></div>
            {resolved?.id === 'seedance' && (
              <div className="field" style={{ width: 90 }}><label>Resolution</label>
                <select className="select" value={resolution} onChange={(e) => setResolution(e.target.value)}><option>480p</option><option>720p</option></select></div>
            )}
          </div>
          {image && resolved?.id === 'seedance' && <Note>With a start image Seedance uses the image's own aspect ratio (adaptive).</Note>}

          {cloud && <CostConfirm checked={confirm} onChange={setConfirm} est={est}
            text={`I understand this spends credit on my ${resolved?.id === 'seedance' ? 'BytePlus ModelArk (Seedance)' : 'cloud'} account.`} />}
          {!cloud && est && <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>{fmtEstimate(est)} · {est.note}</div>}
          {problem && <div className="err" style={{ marginBottom: 8 }}>{problem}</div>}

          <div className="row">
            <button className="btn primary" disabled={!canGo} onClick={generate}>Generate video</button>
            <button className="btn ghost sm" onClick={() => setShowReq((v) => !v)} disabled={!req}><Eye size={12} /> {showReq ? 'Hide' : 'Show'} request</button>
          </div>
          {showReq && !!req && (
            <pre style={{ fontSize: 11, maxHeight: 220, overflow: 'auto', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 5, padding: 8, userSelect: 'text' }}>{JSON.stringify(req, null, 2)}</pre>
          )}
          <JobBar job={job} error={error} onCancel={cancel} />
          {busy && cloud && <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>Cloud jobs can take several minutes. Cancelling only stops a task that is still queued; a started task may still be billed.</div>}
        </div>

        <div style={{ flex: '1 1 340px', minWidth: 0 }}>
          <div className="faint" style={{ fontSize: 12, marginBottom: 4 }}>Result</div>
          {result ? (
            <>
              <video src={result.media.url} controls loop style={{ width: '100%', maxHeight: 360, background: '#000', borderRadius: 6 }} />
              <div className="row" style={{ marginTop: 6 }}>
                <span className="muted grow" style={{ fontSize: 12 }}>{result.media.name} · {result.media.duration.toFixed(1)} s · {result.media.width}x{result.media.height} · saved to the library ({result.provider})</span>
                {onAddMedia && <button className="btn sm" onClick={() => onAddMedia(result.media)}><Plus size={12} /> Use as motion source</button>}
              </div>
            </>
          ) : (
            <div className="faint" style={{ border: '1px dashed var(--line-2)', borderRadius: 6, padding: 24, textAlign: 'center' }}>The generated clip will appear here and in the media library (origin: gen).</div>
          )}
        </div>
      </div>
    </div>
  )
}
