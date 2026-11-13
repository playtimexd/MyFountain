import { useEffect, useState } from 'react'
import { Clapperboard, Copy, ExternalLink, Image as ImageIcon, Send, Trash2, X } from 'lucide-react'
import { api, fmtSize } from '../../api/client'
import type { HubAsset } from './api'
import { AUDIO_KINDS, hubFile } from './api'
import { Badges, KindIcon, toast, useAssetActions } from './common'

export default function AssetDetail({ id, sourceNames, onClose, onDeleted, onTag, kindLabel }: {
  id: string; sourceNames: Record<string, string>; onClose: () => void; onDeleted: () => void; onTag: (t: string) => void; kindLabel: (k: string) => string
}) {
  const [a, setA] = useState<HubAsset | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const act = useAssetActions()
  useEffect(() => { api.get<HubAsset>(`/api/hub/assets/${id}`).then(setA).catch((e) => setErr(e.message)) }, [id])
  useEffect(() => { const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k) }, [onClose])

  const del = async () => {
    if (!a || !confirm(`Delete "${a.name}" and its downloaded files from the hub? This cannot be undone.`)) return
    try { await api.del(`/api/hub/assets/${a.id}`); toast('Deleted'); onDeleted() } catch (e: any) { toast(e.message) }
  }
  const st = a?.stats ?? {}
  const li = a?.license_info
  const mediaImage = a && a.media_id && ['image', 'sprite', 'texture', 'material'].includes(a.kind)
  const mediaAny = a && a.media_id

  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" style={{ width: 'min(1000px, 96vw)' }}>
        <header>
          {a ? <KindIcon kind={a.kind} size={16} /> : null}
          <span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a?.name ?? 'Loading…'}</span>
          <button className="btn ghost icon" onClick={onClose} title="Close"><X size={14} /></button>
        </header>
        <div className="body">
          {err && <div className="err">{err}</div>}
          {a && (
            <div className="hub-detail">
              <div>
                <div className="preview">
                  {a.thumb_url ? <img src={a.thumb_url} alt="" /> : <div className="faint" style={{ textAlign: 'center' }}><KindIcon kind={a.kind} size={56} /><div style={{ fontSize: 11, marginTop: 6 }}>No preview image</div></div>}
                </div>
                {AUDIO_KINDS.has(a.kind) && <audio src={hubFile(a.id)} controls style={{ width: '100%', marginTop: 8 }} />}
                {a.kind === 'video' && <video src={hubFile(a.id)} controls style={{ width: '100%', marginTop: 8 }} />}
                <div className="row" style={{ flexWrap: 'wrap', marginTop: 10 }}>
                  {mediaAny && <button className="btn sm" onClick={() => act.useInVideo(a)}><Clapperboard size={12} />Use in video editor</button>}
                  {mediaImage && <button className="btn sm" onClick={() => act.openInImage(a)}><ImageIcon size={12} />Open in image editor</button>}
                  <button className="btn sm" onClick={() => act.sendToEngine([a.id])}><Send size={12} />Send to my-engine</button>
                  <button className="btn sm" onClick={() => act.copyAttribution(a)}><Copy size={12} />Copy attribution</button>
                  <button className="btn sm danger" onClick={del}><Trash2 size={12} />Delete</button>
                </div>
              </div>
              <div style={{ minWidth: 0 }}>
                <Badges kind={a.kind} license={a.license} cls={a.license_class} rigged={a.rigged} animated={a.animated} clips={st.clips?.length} />
                {a.description && <p className="muted" style={{ fontSize: 12, userSelect: 'text' }}>{a.description}</p>}
                <div className="hub-kv" style={{ margin: '10px 0' }}>
                  <span>Source</span><span>{sourceNames[a.source] ?? a.source}{a.source_url && <> · <a href={a.source_url} target="_blank" rel="noreferrer">open page <ExternalLink size={10} /></a></>}</span>
                  <span>Author</span><span>{a.author_url ? <a href={a.author_url} target="_blank" rel="noreferrer">{a.author || a.author_url}</a> : a.author || '—'}</span>
                  <span>Licence</span><span>{li?.label ?? a.license}{a.license_url && <> · <a href={a.license_url} target="_blank" rel="noreferrer">terms</a></>}</span>
                  <span>Commercial use</span><span style={{ color: a.commercial_ok ? 'var(--ok)' : 'var(--warn)' }}>{a.commercial_ok ? 'Allowed' : 'Not allowed'}</span>
                  <span>Modifications</span><span>{a.modifiable ? 'Allowed' : 'Not allowed'}</span>
                  <span>Credit</span><span>{li?.attribution_required ? 'Required - keep the attribution below with your project' : 'Not required (courtesy)'}</span>
                  <span>Kind</span><span>{kindLabel(a.kind)}{a.formats.length ? ` · ${a.formats.join(', ').toUpperCase()}` : ''} · {fmtSize(a.total_bytes)}</span>
                  {st.polycount ? <><span>Triangles</span><span>{Number(st.polycount).toLocaleString()}</span></> : null}
                  {st.joints ? <><span>Joints</span><span>{st.joints}</span></> : null}
                  {st.clips?.length ? <><span>Clips</span><span>{st.clips.join(', ')}</span></> : null}
                  {st.dimensions_mm ? <><span>Real size</span><span>{st.dimensions_mm.map((d: number) => (d / 1000).toFixed(2)).join(' × ')} m</span></> : null}
                  {st.duration ? <><span>Duration</span><span>{Number(st.duration).toFixed(2)} s{st.fps ? ` @ ${st.fps} fps` : ''}{st.channels ? ` · ${st.channels} ch` : ''}{st.sample_rate ? ` · ${st.sample_rate} Hz` : ''}</span></> : null}
                  {st.texture_size ? <><span>Texture</span><span>{st.width}×{st.height}</span></> : null}
                  {st.units ? <><span>Units</span><span>{st.units}</span></> : null}
                  {st.auto_tags?.length ? <><span>Auto tags</span><span>{st.auto_tags.join(', ')} <span className="faint">(CLIP)</span></span></> : null}
                </div>
                <div className="faint" style={{ fontSize: 11, marginBottom: 4 }}>Attribution (ready to paste)</div>
                <div className="hub-attr">{a.attribution}</div>
                {a.tags.length > 0 && <div className="hub-pill-row" style={{ marginTop: 10 }}>{a.tags.slice(0, 30).map((t) => <button key={t} className="hub-pill" onClick={() => onTag(t)}>{t}</button>)}</div>}
                <div className="faint" style={{ fontSize: 11, margin: '12px 0 4px' }}>Files</div>
                <table className="hub-files"><tbody>
                  {(a.files ?? []).map((f, i) => (
                    <tr key={i}><td>{f.role}</td><td title={f.path}><a href={f.url} target="_blank" rel="noreferrer">{f.path.split(/[\\/]/).pop()}</a></td><td>{f.resolution ?? ''}</td><td>{fmtSize(f.bytes)}</td></tr>))}
                </tbody></table>
                {(a.kind === 'model' || a.kind === 'animation') && <p className="hub-note">Models and animations stay in the hub; the file paths above can be handed to the Animation module for rigging / retargeting.</p>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
