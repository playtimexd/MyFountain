import { useEffect, useMemo, useState } from 'react'
import { Download, Mic } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { Field, JobBar } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import { Note, allClips } from './aiUtil'
import type { AiPanelProps } from './aiUtil'

interface Provider { id: string; name: string; available: boolean; note: string; needs_download?: boolean; download?: string; quality?: string }
interface Voice { id: string; provider: string; name: string; lang: string; gender: string; available: boolean }

const SAMPLE = 'Welcome to CutLab. Type some text here and turn it into a voice-over.'

export default function TtsPanel({ onAddAsset, project, selection, standalone }: AiPanelProps) {
  const [text, setText] = useState('')
  const [voice, setVoice] = useState('auto')
  const [speed, setSpeed] = useState(1)
  const [providers, setProviders] = useState<Provider[]>([])
  const [voices, setVoices] = useState<Voice[]>([])
  const [results, setResults] = useState<MediaRow[]>([])
  const [added, setAdded] = useState<Record<string, string>>({})
  const gen = useJob<{ media: MediaRow; provider: string; voice: string }>()
  const prep = useJob()

  const load = () => {
    api.get<Provider[]>('/api/tts/providers').then(setProviders).catch(() => setProviders([]))
    api.get<Voice[]>('/api/tts/voices').then(setVoices).catch(() => setVoices([]))
  }
  useEffect(load, [])

  const selectedText = useMemo(() => {
    const ids = selection?.clipIds || []
    const c = allClips(project).find((c) => ids.includes(c.id) && c.type === 'text' && c.text?.content)
    return c?.text?.content || ''
  }, [project, selection])

  const sapi = providers.find((p) => p.id === 'sapi')
  const kokoro = providers.find((p) => p.id === 'kokoro')
  const onlySystem = !!sapi?.available && !kokoro?.available
  const byProvider = (id: string) => voices.filter((v) => v.provider === id)
  const label = (v: Voice) => `${v.name}${v.lang ? ` · ${v.lang}` : ''}${v.gender ? ` · ${v.gender}` : ''}`

  const generate = async () => {
    try {
      const done = await gen.run(() => api.post('/api/tts/generate', { text, voice: voice === 'auto' ? undefined : voice, speed, provider: 'auto' }))
      if (done.result?.media) setResults((r) => [done.result!.media, ...r])
    } catch { /* shown by JobBar */ }
  }
  const download = async () => {
    if (!window.confirm(`Download the Kokoro-82M voice model (${kokoro?.download || 'about 170 MB'})?`)) return
    try { await prep.run(() => api.post('/api/tts/prepare', { pack: 'zh', variant: 'int8' })); load() } catch { /* shown by JobBar */ }
  }

  return (
    <div style={{ padding: 12, overflow: 'auto', height: '100%' }}>
      <div className="row" style={{ marginBottom: 10 }}><Mic size={16} /><b>Text to Speech</b><span className="faint" style={{ fontSize: 11 }}>voice-over, runs locally</span></div>

      {onlySystem && (
        <div className="panel" style={{ padding: 8, marginBottom: 10, borderColor: 'var(--warn)' }}>
          <div style={{ fontSize: 12, color: 'var(--warn)' }}>
            Only the built-in Windows voices are available. They sound fairly robotic. For natural voices, download the local Kokoro-82M model.
          </div>
          {kokoro?.needs_download && (
            <button className="btn sm" style={{ marginTop: 6 }} disabled={prep.busy} onClick={download}>
              <Download size={13} />Download Kokoro ({kokoro.download || '~170 MB'})
            </button>
          )}
          {kokoro && !kokoro.needs_download && <Note>{kokoro.note}</Note>}
          <JobBar job={prep.job} error={prep.error} onCancel={prep.cancel} />
        </div>
      )}
      {!onlySystem && !sapi?.available && !kokoro?.available && providers.length > 0 && (
        <Note tone="warn">No voices are available. {sapi?.note}</Note>
      )}

      <Field label="Text">
        <textarea className="textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder={SAMPLE} />
      </Field>
      <div className="row" style={{ marginTop: -4, marginBottom: 8, fontSize: 11 }}>
        <span className="faint grow">{text.length} characters</span>
        {selectedText && <button className="btn sm ghost" onClick={() => setText(selectedText)}>Use selected text clip</button>}
        <button className="btn sm ghost" onClick={() => setText(SAMPLE)}>Sample</button>
      </div>

      <Field label="Voice">
        <select className="select" value={voice} onChange={(e) => setVoice(e.target.value)}>
          <option value="auto">Auto (best voice for the text)</option>
          {providers.filter((p) => p.id === 'sapi' || p.id === 'kokoro').map((p) => (
            <optgroup key={p.id} label={`${p.name}${p.available ? '' : ' (unavailable)'}`}>
              {p.available
                ? byProvider(p.id).map((v) => <option key={v.id} value={v.id}>{label(v)}</option>)
                : <option disabled>{p.needs_download ? 'Model not downloaded' : 'Not available'}</option>}
            </optgroup>
          ))}
        </select>
      </Field>
      <Field label={`Speed: ${speed.toFixed(2)}x`}>
        <input type="range" min={0.5} max={2} step={0.05} value={speed} onChange={(e) => setSpeed(+e.target.value)} />
      </Field>

      <button className="btn primary" style={{ width: '100%', justifyContent: 'center' }} disabled={gen.busy || !text.trim()} onClick={generate}>
        <Mic size={14} />{gen.busy ? 'Generating...' : 'Generate voice-over'}
      </button>
      <JobBar job={gen.job} error={gen.error} onCancel={gen.cancel} />

      {results.length > 0 && (
        <div style={{ marginTop: 14 }}>
          {results.map((m) => (
            <div key={m.id} className="panel" style={{ padding: 8, marginBottom: 8 }}>
              <div className="row" style={{ marginBottom: 6 }}>
                <span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name}</span>
                <span className="chip">{m.duration.toFixed(1)}s</span>
              </div>
              <audio controls src={m.url} style={{ width: '100%', height: 32 }} />
              <div className="row" style={{ marginTop: 6, flexWrap: 'wrap' }}>
                {standalone
                  ? <span className="chip" style={{ color: 'var(--ok)' }}>Saved to library</span>
                  : <>
                    <button className="btn sm" onClick={() => { onAddAsset(m, { toTimeline: false }); setAdded((a) => ({ ...a, [m.id]: 'lib' })) }}>Add to library</button>
                    {project && <button className="btn sm primary" onClick={() => { onAddAsset(m, { toTimeline: true }); setAdded((a) => ({ ...a, [m.id]: 'tl' })) }}>Add to timeline</button>}
                    {added[m.id] && <span className="chip" style={{ color: 'var(--ok)' }}>Added</span>}
                  </>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
