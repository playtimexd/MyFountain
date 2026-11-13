import { useEffect, useState } from 'react'
import { ExternalLink, KeyRound, Save, X } from 'lucide-react'
import { api } from '../../api/client'
import type { SourceInfo } from './api'
import { toast } from './common'

interface AiStatus { clip: { ready: boolean; message: string }; text_embed: { ready: boolean; message: string; model?: string } }
const KEY_HELP: Record<string, string> = {
  freesound: 'https://freesound.org/apiv2/apply', polypizza: 'https://poly.pizza/settings/api',
  sketchfab: 'https://sketchfab.com/settings/password', google_fonts: 'https://developers.google.com/fonts/docs/developer_api',
}

export default function Sources({ sources, reload }: { sources: SourceInfo[]; reload: () => void }) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [ai, setAi] = useState<AiStatus | null>(null)
  const [engine, setEngine] = useState<{ running: boolean; message: string } | null>(null)
  useEffect(() => {
    api.get<AiStatus>('/api/hub/ai/status').then(setAi).catch(() => {})
    api.get('/api/hub/engine/status').then(setEngine).catch(() => {})
  }, [])

  const saveKey = async (name: string, value: string) => {
    try {
      await api.put('/api/hub/keys', { [name]: value })
      setDrafts((d) => ({ ...d, [name]: '' })); reload()
      toast(value ? 'Key saved (it is never shown again)' : 'Key removed')
    } catch (e: any) { toast(e.message) }
  }

  return (
    <div className="hub-scroll">
      <div className="hub-section">
        <h3>Sources</h3>
        <table className="hub-table">
          <thead><tr><th style={{ width: 150 }}>Source</th><th style={{ width: 110 }}>Status</th><th style={{ width: 70 }}>In hub</th><th>Terms &amp; limits</th><th style={{ width: 300 }}>API key</th></tr></thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.id}>
                <td><b>{s.name}</b><div className="faint" style={{ fontSize: 11 }}>{s.homepage && <a href={s.homepage} target="_blank" rel="noreferrer">{s.homepage.replace(/^https?:\/\//, '')}</a>}</div>
                  <div className="faint" style={{ fontSize: 11 }}>{s.kinds.join(', ')}</div></td>
                <td><span style={{ color: s.available ? 'var(--ok)' : 'var(--warn)' }}>{s.available ? (s.automated ? 'Ready' : 'Catalog') : 'Needs key'}</span>
                  <div className="faint" style={{ fontSize: 11 }}>{s.note}</div></td>
                <td>{s.items}</td>
                <td className="hub-note" style={{ fontSize: 12 }}>{s.terms} {s.terms_url && <a href={s.terms_url} target="_blank" rel="noreferrer">Read terms <ExternalLink size={10} /></a>}</td>
                <td>
                  {s.key_name ? (
                    <>
                      <div className="row">
                        <input className="input" type="password" autoComplete="off" value={drafts[s.key_name] ?? ''} placeholder={s.key_set ? 'Key is set - type to replace' : 'Paste API key'}
                          onChange={(e) => setDrafts((d) => ({ ...d, [s.key_name!]: e.target.value }))} />
                        <button className="btn sm" disabled={!drafts[s.key_name]} onClick={() => saveKey(s.key_name!, drafts[s.key_name!])} title="Save"><Save size={12} /></button>
                        {s.key_set && <button className="btn sm ghost" onClick={() => saveKey(s.key_name!, '')} title="Remove key"><X size={12} /></button>}
                      </div>
                      <div className="faint" style={{ fontSize: 11, marginTop: 3 }}><KeyRound size={10} /> {s.key_set ? 'set' : 'not set'}{s.needs_key ? ' · required' : ' · optional'}
                        {KEY_HELP[s.key_name] && <> · <a href={KEY_HELP[s.key_name]} target="_blank" rel="noreferrer">get a key</a></>}</div>
                    </>
                  ) : <span className="faint">no key needed</span>}
                </td>
              </tr>))}
          </tbody>
        </table>
        <p className="hub-note">Keys are stored only in data/hub/keys.json on this PC, are sent only to the service they belong to, and are never shown or logged.</p>
      </div>
      <div className="hub-section">
        <h3>Pipelines</h3>
        <div className="hub-kv">
          <span>Auto-tagging</span><span style={{ color: ai?.clip.ready ? 'var(--ok)' : 'var(--warn)' }}>{ai ? ai.clip.message : '…'}</span>
          <span>Text embeddings</span><span style={{ color: ai?.text_embed.ready ? 'var(--ok)' : 'var(--warn)' }}>{ai ? `${ai.text_embed.message}${ai.text_embed.model ? ` (${ai.text_embed.model})` : ''}` : '…'}</span>
          <span>my-engine</span><span style={{ color: engine?.running ? 'var(--ok)' : 'var(--warn)' }}>{engine?.message ?? '…'}</span>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn sm" onClick={async () => { const j = await api.post('/api/hub/enrich', {}); toast(`Started: ${j.title}`) }}>Re-run auto-tagging for assets missing tags/embeddings</button>
        </div>
      </div>
    </div>
  )
}
