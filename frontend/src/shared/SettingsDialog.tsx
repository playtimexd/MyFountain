import { useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'
import { api } from '../api/client'
import { Field } from './ui'

const MASK = '••••••••'
type S = Record<string, string>

interface Group { title: string; note?: string; fields: { key: string; label: string; secret?: boolean; options?: string[]; placeholder?: string }[] }

const GROUPS: Group[] = [
  { title: 'Cloud LLM (optional)', note: 'Default is local Ollama — nothing leaves your machine. With "anthropic", prompts and any attached frames (e.g. animation QA) are sent to the Anthropic API. Tip: if you drive CutLab from Claude Code or Codex over MCP you do not need a key at all.',
    fields: [
      { key: 'llm_provider', label: 'Provider', options: ['ollama', 'anthropic'] },
      { key: 'anthropic_api_key', label: 'Anthropic API key', secret: true, placeholder: 'sk-ant-…' },
      { key: 'anthropic_model', label: 'Anthropic model' },
      { key: 'ollama_url', label: 'Ollama URL' },
      { key: 'llm_model', label: 'Local model (Ollama)' },
    ] },
  { title: '3D character generation (optional, uses credits)', note: 'Text/image → 3D model, optionally auto-rigged. Both services bill your own account; CutLab shows the cost and asks before every generation.',
    fields: [
      { key: 'gen3d_provider', label: 'Provider', options: ['auto', 'meshy', 'tripo'] },
      { key: 'meshy_api_key', label: 'Meshy API key', secret: true },
      { key: 'tripo_api_key', label: 'Tripo API key', secret: true },
    ] },
  { title: 'Reference video generation (optional)', note: 'Generates a fixed-camera reference clip to extract motion from. Seedance is a paid cloud API; local models run through ComfyUI.',
    fields: [
      { key: 'videogen_provider', label: 'Provider', options: ['auto', 'seedance', 'local'] },
      { key: 'seedance_api_key', label: 'Seedance API key', secret: true },
      { key: 'seedance_base_url', label: 'Seedance base URL' },
      { key: 'seedance_model', label: 'Seedance model / endpoint id' },
      { key: 'comfyui_url', label: 'ComfyUI URL' },
    ] },
  { title: 'Downloads & stock', fields: [
    { key: 'cookies_from_browser', label: 'Login cookies for downloads', options: ['', 'chrome', 'edge', 'firefox', 'brave'] },
    { key: 'proxy', label: 'Proxy', placeholder: 'http://127.0.0.1:7890' },
    { key: 'pexels_api_key', label: 'Pexels API key', secret: true },
    { key: 'pixabay_api_key', label: 'Pixabay API key', secret: true },
  ] },
  { title: 'Local engines', fields: [
    { key: 'whisper_model', label: 'Whisper model', options: ['tiny', 'base', 'small', 'medium', 'large-v3'] },
    { key: 'acestep_url', label: 'ACE-Step URL' },
    { key: 'blender_exe', label: 'Blender executable', placeholder: 'auto-detected' },
  ] },
]

export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [vals, setVals] = useState<S>({})
  const [orig, setOrig] = useState<S>({})
  const [caps, setCaps] = useState<any>(null)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get<S>('/api/settings').then((s) => { setVals(s); setOrig(s) }).catch((e) => setErr(e.message))
    api.get('/api/capabilities').then(setCaps).catch(() => {})
  }, [])
  useEffect(() => { const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])

  const save = async () => {
    setErr('')
    const patch: S = {}
    for (const k of Object.keys(vals)) if (vals[k] !== orig[k]) patch[k] = vals[k]
    try { const s = await api.put<S>('/api/settings', { values: patch }); setVals(s); setOrig(s); setSaved(true); setTimeout(() => setSaved(false), 1500) }
    catch (e: any) { setErr(e.message) }
  }
  const status = (label: string, ok: boolean | undefined) => <span className="chip" style={{ color: ok ? 'var(--ok)' : 'var(--text-faint)' }}>{ok ? '●' : '○'} {label}</span>

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: 'min(760px, 94vw)', height: '86vh' }}>
        <header>Settings<span className="grow" /><button className="btn ghost icon" onClick={onClose}><X size={16} /></button></header>
        <div className="body" style={{ flex: 1 }}>
          {caps && <div className="row" style={{ flexWrap: 'wrap', marginBottom: 14 }}>
            {status('ffmpeg' + (caps.ffmpeg?.nvenc ? ' + NVENC' : ''), true)}
            {status('Blender', caps.blender?.available)}
            {status('Ollama', caps.llm?.ollama)}
            {status('ACE-Step', caps.music?.acestep_api)}
            {status('ComfyUI', caps.cloud?.comfyui)}
            {status('Anthropic key', caps.cloud?.anthropic_key_set)}
            {status('Meshy key', caps.cloud?.meshy_key_set)}
            {status('Tripo key', caps.cloud?.tripo_key_set)}
            {status('Seedance key', caps.cloud?.seedance_key_set)}
          </div>}
          {GROUPS.map((g) => (
            <div key={g.title} className="panel" style={{ padding: 14, marginBottom: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{g.title}</div>
              {g.note && <div className="faint" style={{ fontSize: 12, marginBottom: 10 }}>{g.note}</div>}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 12px' }}>
                {g.fields.map((f) => (
                  <Field key={f.key} label={f.label}>
                    {f.options ? (
                      <select className="select" value={vals[f.key] ?? ''} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })}>
                        {f.options.map((o) => <option key={o} value={o}>{o || '(none)'}</option>)}
                      </select>
                    ) : (
                      <input className="input" type={f.secret ? 'password' : 'text'} autoComplete="off" spellCheck={false}
                        value={vals[f.key] ?? ''} placeholder={f.placeholder}
                        onFocus={(e) => { if (f.secret && vals[f.key] === MASK) { setVals({ ...vals, [f.key]: '' }); e.currentTarget.placeholder = 'Enter a new key to replace' } }}
                        onBlur={() => { if (f.secret && vals[f.key] === '' && orig[f.key] === MASK) setVals({ ...vals, [f.key]: MASK }) }}
                        onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
                    )}
                  </Field>
                ))}
              </div>
            </div>
          ))}
          <div className="faint" style={{ fontSize: 11 }}>Keys are stored only in data/settings.json on this machine and are never sent back to the browser (masked as ••••••••).</div>
        </div>
        <div className="row" style={{ padding: '10px 16px', borderTop: '1px solid var(--line)' }}>
          <span className="err grow" style={{ margin: 0 }}>{err}</span>
          {saved && <span style={{ color: 'var(--ok)' }}><Check size={14} /> Saved</span>}
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" onClick={save}>Save</button>
        </div>
      </div>
    </div>
  )
}
