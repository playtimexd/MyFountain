import { useEffect, useState } from 'react'
import { Music, Sparkles } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { Field, JobBar } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import { Chips, Note } from './aiUtil'
import type { AiPanelProps } from './aiUtil'

interface Provider { id: string; name: string; available: boolean; note: string }

const MOODS = [
  { v: 'happy', label: 'Happy' }, { v: 'calm', label: 'Calm' }, { v: 'sad', label: 'Sad' }, { v: 'epic', label: 'Epic' },
  { v: 'dark', label: 'Tense' }, { v: 'romantic', label: 'Romantic' }, { v: 'energetic', label: 'Energetic' }, { v: 'mysterious', label: 'Mysterious' },
]
const GENRES = [
  { v: 'pop', label: 'Pop' }, { v: 'lofi', label: 'Lo-fi' }, { v: 'edm', label: 'Electronic' }, { v: 'cinematic', label: 'Cinematic' },
  { v: 'ambient', label: 'Ambient' }, { v: 'piano', label: 'Piano' }, { v: 'rock', label: 'Rock' }, { v: 'jazz', label: 'Jazz' },
  { v: 'hiphop', label: 'Hip-hop' }, { v: 'folk', label: 'Folk' },
]

export default function MusicPanel({ onAddAsset, project, standalone }: AiPanelProps) {
  const [prompt, setPrompt] = useState('')
  const [mood, setMood] = useState<string>('')
  const [genre, setGenre] = useState<string>('')
  const [duration, setDuration] = useState(30)
  const [bpm, setBpm] = useState<string>('')
  const [provider, setProvider] = useState('auto')
  const [providers, setProviders] = useState<Provider[]>([])
  const [results, setResults] = useState<MediaRow[]>([])
  const [added, setAdded] = useState<Record<string, string>>({})
  const { job, error, run, cancel, busy } = useJob<{ media: MediaRow; provider: string; fallback_reason?: string; info?: Record<string, unknown> }>()
  const [info, setInfo] = useState<string>('')

  const loadProviders = () => api.get<Provider[]>('/api/music/providers').then(setProviders).catch(() => setProviders([]))
  useEffect(() => { loadProviders() }, [])

  const cur = providers.find((p) => p.id === provider)

  const generate = async () => {
    setInfo('')
    try {
      const done = await run(() => api.post('/api/music/generate', {
        prompt: prompt.trim() || (mood || genre ? '' : 'relaxing background music'), duration, instrumental: true,
        bpm: bpm ? Number(bpm) : undefined, mood: mood || undefined, genre: genre || undefined, provider,
      }))
      const r = done.result
      if (r?.media) {
        setResults((x) => [r.media, ...x])
        const i = r.info as Record<string, any> | undefined
        setInfo(`${r.provider}${i?.bpm ? ` · ${i.bpm} BPM` : ''}${i?.key ? ` · ${i.key}` : ''}${r.fallback_reason ? ` · fell back (${r.fallback_reason.slice(0, 40)})` : ''}`)
      }
    } catch { /* shown by JobBar */ }
  }

  return (
    <div style={{ padding: 12, overflow: 'auto', height: '100%' }}>
      <div className="row" style={{ marginBottom: 10 }}><Music size={16} /><b>AI Music</b><span className="faint" style={{ fontSize: 11 }}>generated locally · royalty-free</span></div>
      <Field label="Description (style / scene / instruments, optional)">
        <textarea className="textarea" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="e.g. travel vlog, upbeat guitar and drums" />
      </Field>
      <Field label="Mood"><Chips options={MOODS} value={mood} onChange={setMood} /></Field>
      <Field label="Genre"><Chips options={GENRES} value={genre} onChange={setGenre} /></Field>
      <Field label={`Duration: ${duration} s`}>
        <input type="range" min={10} max={180} step={5} value={duration} onChange={(e) => setDuration(+e.target.value)} />
      </Field>
      <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
        <div className="field grow"><label>BPM (blank = auto)</label>
          <input className="input" type="number" min={50} max={200} value={bpm} onChange={(e) => setBpm(e.target.value)} placeholder="auto" /></div>
        <div className="field grow"><label>Engine</label>
          <select className="select" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="auto">Auto (best available)</option>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.name.split(' (')[0]}{p.available ? '' : ' (unavailable)'}</option>)}
          </select></div>
      </div>
      {provider !== 'auto' && cur && <Note tone={cur.available ? 'ok' : 'warn'}>{cur.note}</Note>}
      {provider === 'auto' && providers.length > 0 && (
        <div className="row" style={{ flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
          {providers.map((p) => <span key={p.id} className="chip" title={p.note} style={{ color: p.available ? 'var(--ok)' : undefined }}>{p.available ? '●' : '○'} {p.id}</span>)}
        </div>
      )}
      <button className="btn primary" style={{ width: '100%', justifyContent: 'center' }} disabled={busy} onClick={generate}>
        <Sparkles size={14} />{busy ? 'Generating...' : 'Generate music'}
      </button>
      <JobBar job={job} error={error} onCancel={cancel} />

      {results.length > 0 && <div style={{ marginTop: 14 }}>
        {info && <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>{info}</div>}
        {results.map((m) => (
          <div key={m.id} className="panel" style={{ padding: 8, marginBottom: 8 }}>
            <div className="row" style={{ marginBottom: 6 }}>
              <span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name}</span>
              <span className="chip">{m.duration.toFixed(0)}s</span>
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
      </div>}
    </div>
  )
}
