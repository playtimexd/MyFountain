import { useCallback, useEffect, useState } from 'react'
import { Download, Link2, Loader2, Scissors, Search } from 'lucide-react'
import { api, fmtTime, useJob } from '../../api/client'
import type { JobInfo, MediaRow } from '../../types/api'
import { emit, on } from '../../shared/bus'
import ActivityHistory from '../../shared/projects/ActivityHistory'
import SegmentDialog from '../../shared/SegmentDialog'
import { Field, JobBar } from '../../shared/ui'

interface Info { url: string; site: string; title?: string; thumbnail?: string; duration?: number; uploader?: string; heights: number[] }
const SITES = ['YouTube', 'Bilibili', 'Douyin', 'Xiaohongshu', 'Facebook', 'X / Twitter', 'Instagram', 'TikTok', 'Weibo', 'Kuaishou']

export default function Downloader({ onGoLibrary }: { onGoLibrary: () => void }) {
  const [text, setText] = useState('')
  const [info, setInfo] = useState<Info | null>(null)
  const [infoErr, setInfoErr] = useState('')
  const [parsing, setParsing] = useState(false)
  const [quality, setQuality] = useState('best')
  const [cookies, setCookies] = useState('')
  const [done, setDone] = useState<MediaRow | null>(null)
  const [seg, setSeg] = useState<MediaRow | null>(null)
  const [history, setHistory] = useState<MediaRow[]>([])
  const dl = useJob<{ media: MediaRow }>()

  const loadHistory = useCallback(() => api.get<MediaRow[]>('/api/media?origin=download&limit=30').then(setHistory).catch(() => {}), [])
  useEffect(() => { loadHistory(); return on('cutlab:media-changed', loadHistory) }, [loadHistory])
  useEffect(() => { api.get('/api/settings').then((s) => setCookies(s.cookies_from_browser || '')).catch(() => {}) }, [])

  const parse = async () => {
    setParsing(true); setInfoErr(''); setInfo(null); setDone(null)
    try { setInfo(await api.post<Info>('/api/download/info', { url: text, cookies_from_browser: cookies || undefined })) }
    catch (e: any) { setInfoErr(e.message) } finally { setParsing(false) }
  }
  const start = async () => {
    setDone(null)
    try {
      const j = await dl.run(() => api.post<JobInfo<{ media: MediaRow }>>('/api/download', { url: text, quality, cookies_from_browser: cookies || undefined }))
      if (j.result) { setDone(j.result.media); emit('cutlab:media-changed', { media: j.result.media }) }
    } catch { /* JobBar */ }
  }
  const saveCookies = (v: string) => { setCookies(v); api.put('/api/settings', { values: { cookies_from_browser: v } }).catch(() => {}) }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(420px, 560px) 1fr', gap: 16, padding: 16, height: '100%', minHeight: 0 }}>
      <div className="panel" style={{ padding: 16, overflow: 'auto' }}>
        <div className="row" style={{ marginBottom: 10 }}><Link2 size={16} /><b>Paste a video link</b></div>
        <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>Supports {SITES.join(' · ')} and 1000+ more sites; you can paste the full share text copied from an app.</div>
        <textarea className="textarea" rows={3} placeholder="https://www.youtube.com/watch?v=…  /  https://www.bilibili.com/video/BV…  /  https://v.douyin.com/…  /  http://xhslink.com/…"
          value={text} onChange={(e) => setText(e.target.value)} style={{ userSelect: 'text' }} />
        <div className="row" style={{ margin: '10px 0' }}>
          <button className="btn" disabled={!text.trim() || parsing} onClick={parse}>{parsing ? <Loader2 size={14} className="spin" /> : <Search size={14} />}Parse</button>
          <button className="btn primary" disabled={!text.trim() || dl.busy} onClick={start}><Download size={14} />Download</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="Quality">
            <select className="select" value={quality} onChange={(e) => setQuality(e.target.value)}>
              <option value="best">Best quality</option><option value="2160">≤ 4K</option><option value="1440">≤ 2K</option><option value="1080">≤ 1080p</option>
              <option value="720">≤ 720p</option><option value="480">≤ 480p</option><option value="audio">Audio only (MP3)</option>
            </select>
          </Field>
          <Field label="Login cookies (read from browser)">
            <select className="select" value={cookies} onChange={(e) => saveCookies(e.target.value)}>
              <option value="">None</option><option value="chrome">Chrome</option><option value="edge">Edge</option><option value="firefox">Firefox</option><option value="brave">Brave</option>
            </select>
          </Field>
        </div>
        <div className="faint" style={{ fontSize: 11 }}>Douyin / Xiaohongshu / Bilibili HD / private Facebook content usually need login cookies. Close Chrome/Edge before reading its cookies. Only download content you have the right to use.</div>
        {infoErr && <div className="err">{infoErr}</div>}
        {info && (
          <div className="panel" style={{ display: 'flex', gap: 12, padding: 10, marginTop: 12, background: 'var(--bg-2)' }}>
            {info.thumbnail && <img src={info.thumbnail} referrerPolicy="no-referrer" style={{ width: 140, height: 80, objectFit: 'cover', borderRadius: 6 }} />}
            <div className="grow">
              <div style={{ fontWeight: 600, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{info.title}</div>
              <div className="muted" style={{ fontSize: 12 }}>{info.uploader} · {info.duration ? fmtTime(info.duration) : 'Unknown duration'} · {info.site}</div>
              {!!info.heights.length && <div className="faint" style={{ fontSize: 11 }}>Available qualities: {info.heights.slice(0, 6).map((h) => `${h}p`).join(' / ')}</div>}
            </div>
          </div>
        )}
        <JobBar job={dl.job} error={dl.error} onCancel={dl.cancel} />
        {done && (
          <div className="panel" style={{ padding: 10, marginTop: 12, background: 'var(--bg-2)' }}>
            <div className="row">
              <img src={done.thumb} style={{ width: 120, aspectRatio: '16/9', objectFit: 'cover', borderRadius: 6 }} />
              <div className="grow"><div style={{ fontWeight: 600 }}>{done.name}</div><div className="muted" style={{ fontSize: 12 }}>{done.width}×{done.height} · {fmtTime(done.duration)} · Added to library</div></div>
            </div>
            <div className="row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
              {done.kind === 'video' && <button className="btn sm" onClick={() => setSeg(done)}><Scissors size={13} />Smart split</button>}
              {done.kind === 'video' && <button className="btn sm primary" onClick={() => emit('cutlab:add-to-timeline', { media: done })}>Add to timeline</button>}
              <button className="btn sm" onClick={onGoLibrary}>Go to library</button>
            </div>
          </div>
        )}
        <div style={{ marginTop: 16 }}><ActivityHistory areas={['download', 'segment']} title="Download & split history" empty="Finished and failed downloads are listed here." limit={30} compact /></div>
      </div>
      <div className="panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="panel-title">Recent downloads</div>
        <div style={{ padding: 12, overflow: 'auto' }}>
          {!history.length && <div className="faint" style={{ textAlign: 'center', padding: 30 }}>No downloads yet</div>}
          <div className="media-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))' }}>
            {history.map((m) => (
              <div key={m.id} className="media-card" onClick={() => setSeg(m)} title={m.source_url || m.name}>
                <img src={m.thumb} loading="lazy" /><span className="dur">{fmtTime(m.duration)}</span>
                <div className="cap">{m.name}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
      {seg && <SegmentDialog media={seg} onClose={() => setSeg(null)} />}
    </div>
  )
}
