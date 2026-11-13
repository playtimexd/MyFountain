import { useCallback, useEffect, useState } from 'react'
import { Check, Download, ExternalLink, Gift, X } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'

/** Free characters / base meshes from official sources (one click), plus pointers to packs that must be downloaded by hand. */
interface Pack { id: string; name: string; desc: string; license: string; author: string; source_url: string; size_mb: number; rigged: boolean; files: number; installed: number; available?: boolean; note?: string; local_unreal?: boolean }
interface Manual { name: string; license: string; url: string; note: string }

export default function StarterPacks({ onClose, onInstalled }: { onClose: () => void; onInstalled?: () => void }) {
  const [packs, setPacks] = useState<Pack[]>([])
  const [manual, setManual] = useState<Manual[]>([])
  const [busyId, setBusyId] = useState('')
  const [done, setDone] = useState<Record<string, string>>({})
  const job = useJob<any>()

  const load = useCallback(() => api.get<{ packs: Pack[]; manual: Manual[] }>('/api/hub/starter').then((r) => { setPacks(r.packs); setManual(r.manual) }).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  useEffect(() => { const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])

  const install = async (p: Pack) => {
    setBusyId(p.id)
    try {
      const j = await job.run(() => api.post(`/api/hub/starter/${p.id}`))
      const r = j.result || {}
      const n = r.imported?.added ?? 0
      const rigs = (r.rigs || []).filter((x: any) => x.id).map((x: any) => x.name)
      setDone((d) => ({ ...d, [p.id]: `${n} model${n === 1 ? '' : 's'} added${rigs.length ? ` · in Animation: ${rigs.join(', ')}` : ''}` }))
      load(); onInstalled?.()
    } catch { /* JobBar */ } finally { setBusyId('') }
  }

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: 'min(820px, 94vw)', maxHeight: '88vh' }}>
        <header><Gift size={16} />Free characters & base meshes<span className="grow" /><button className="btn ghost icon" onClick={onClose}><X size={16} /></button></header>
        <div className="body" style={{ overflow: 'auto' }}>
          <div className="faint" style={{ fontSize: 12, marginBottom: 10 }}>Downloaded from the official sources, filed in the Asset Hub with licence and credits. Rigged characters also appear in Animation.</div>
          {packs.map((p) => (
            <div key={p.id} className="panel" style={{ padding: 10, marginBottom: 8 }}>
              <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                <div className="grow" style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</div>
                  <div className="faint" style={{ fontSize: 12, marginTop: 2 }}>{p.desc}</div>
                  <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
                    <span className="chip" style={{ fontSize: 10 }}>{p.license.replace(/^custom:/, '')}</span> {p.rigged ? 'rigged' : 'not rigged'} · {p.local_unreal ? (p.note || 'exported from your Unreal install') : `~${p.size_mb < 1 ? '<1' : Math.round(p.size_mb)} MB`} · by {p.author} ·{' '}
                    <a href={p.source_url} target="_blank" rel="noreferrer">source <ExternalLink size={10} /></a>
                  </div>
                  {done[p.id] && <div style={{ fontSize: 12, color: 'var(--ok)', marginTop: 4 }}>{done[p.id]}</div>}
                </div>
                <button className={`btn sm ${p.installed ? '' : 'primary'}`} disabled={!!busyId || p.available === false} onClick={() => install(p)}>
                  {p.installed ? <><Check size={13} />Installed ({p.installed})</> : <><Download size={13} />{busyId === p.id ? 'Installing…' : 'Install'}</>}
                </button>
              </div>
            </div>
          ))}
          <JobBar job={job.job} error={job.error} onCancel={job.cancel} />
          <div style={{ fontWeight: 600, fontSize: 13, margin: '14px 0 6px' }}>More free packs (download yourself, then Library → Asset Hub → Import pack)</div>
          {manual.map((m) => (
            <div key={m.url} style={{ fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--line)' }}>
              <a href={m.url} target="_blank" rel="noreferrer">{m.name} <ExternalLink size={10} /></a> <span className="chip" style={{ fontSize: 10 }}>{m.license}</span>
              <div className="faint">{m.note}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
