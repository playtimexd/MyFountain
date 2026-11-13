import { Loader2, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { create } from 'zustand'
import { JobBar } from '../../shared/ui'
import type { JobInfo, MediaRow } from '../../types/api'
import { aiInpaint, aiRemoveBg, aiUpscale, cssToCanvas, fetchCatalog, fetchImages, type CatalogLite } from './ai'
import { Seg } from './controls'
import { sendToLibrary } from './io'
import { insertImageUrl, placeCanvas } from './ops'
import { curDoc, useUI } from './store'

export type AiAction = 'removebg-layer' | 'removebg-mask' | 'inpaint' | 'upscale2' | 'upscale4'

/** Pending request from the AI menu; the panel runs it once it is open. */
const useAiReq = create<{ req: { action: AiAction; n: number } | null }>(() => ({ req: null }))

/** Open (and reveal) the AI panel in the dock; optionally run an action there. */
export function openAiPanel(action?: AiAction) {
  useUI.setState((s) => ({ panelsOpen: { ...s.panelsOpen, ai: true } }))
  if (action) useAiReq.setState((s) => ({ req: { action, n: (s.req?.n ?? 0) + 1 } }))
  requestAnimationFrame(() => document.querySelector('.ie-dp[data-panel=ai]')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
}

export function AiPanel() {
  useUI((s) => s.rev)
  const req = useAiReq((s) => s.req)
  const d = curDoc()
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [job, setJob] = useState<JobInfo | null>(null)
  const [scale, setScale] = useState<'2' | '4'>('2')
  const [prompt, setPrompt] = useState('')
  const [engines, setEngines] = useState<{ rb?: string; ip?: string }>({})
  const [mode, setMode] = useState<'layer' | 'mask'>('layer')
  const run = async (name: string, fn: () => Promise<void>) => {
    if (busy) return
    setBusy(name); setErr(null); setJob(null); setMsg('')
    try { await fn() } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy(null); setMsg('') }
  }
  const dis = !d || !d.active || !!busy
  const removeBg = (m: 'layer' | 'mask') => d && run('Removing background', async () => { const r = await aiRemoveBg(d, m, setMsg); setEngines((e) => ({ ...e, rb: r.engine })) })
  const inpaint = () => d && run('Inpainting', async () => { const r = await aiInpaint(d, prompt, setMsg); setEngines((e) => ({ ...e, ip: r.engine })) })
  const upscale = (s: 2 | 4) => d && run('Upscaling', () => aiUpscale(d, s, (j, m) => { setJob(j); setMsg(m) }))
  // run a request coming from the AI menu
  useEffect(() => {
    if (!req) return
    useAiReq.setState({ req: null })
    if (!d || !d.active) { setErr('Open an image and select a layer first'); return }
    switch (req.action) {
      case 'removebg-layer': setMode('layer'); void removeBg('layer'); break
      case 'removebg-mask': setMode('mask'); void removeBg('mask'); break
      case 'inpaint': if (!d.sel) setErr('Make a selection (or paint with the AI Mark Brush) over the area to erase first'); else void inpaint(); break
      case 'upscale2': setScale('2'); void upscale(2); break
      case 'upscale4': setScale('4'); void upscale(4); break
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req])
  return (
    <div className="ie-ai">
      <div className="faint" style={{ fontSize: 11.5, marginBottom: 8 }}>Scope: the selection if there is one, otherwise the active layer. Results are added as new layers / masks and can be undone.</div>
      <div className="card">
        <div className="row"><b className="grow">Remove Background</b>
          <Seg value={mode} onChange={setMode} options={[['layer', 'New layer'], ['mask', 'As mask']]} /></div>
        <button className="btn primary" style={{ marginTop: 6, width: '100%', justifyContent: 'center' }} disabled={dis}
          onClick={() => removeBg(mode)}>{busy === 'Removing background' ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}Remove Background</button>
      </div>
      <div className="card">
        <b>AI Erase / Inpaint</b>
        <div className="faint" style={{ fontSize: 11.5, margin: '2px 0 6px' }}>Select the object or watermark to remove with a selection tool, or paint it with the AI Mark Brush (A).</div>
        <input className="input" placeholder="(optional) fill prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <div className="row" style={{ marginTop: 6 }}>
          <button className="btn primary grow" style={{ justifyContent: 'center' }} disabled={dis || !d?.sel} title={!d?.sel ? 'Make a selection first' : ''}
            onClick={inpaint}>{busy === 'Inpainting' ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}Erase / Inpaint</button>
          <button className="btn" onClick={() => useUI.getState().setTool('aibrush')}>AI Mark Brush</button>
        </div>
      </div>
      <div className="card">
        <div className="row"><b className="grow">Upscale</b><Seg value={scale} onChange={setScale} options={[['2', '2×'], ['4', '4×']]} /></div>
        <button className="btn primary" style={{ marginTop: 6, width: '100%', justifyContent: 'center' }} disabled={dis}
          onClick={() => upscale(Number(scale) as 2 | 4)}>{busy === 'Upscaling' ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}Super-resolution Upscale</button>
      </div>
      <div className="card">
        <b>Send</b>
        <div className="row" style={{ marginTop: 6 }}>
          <button className="btn grow" disabled={!d} onClick={() => d && sendToLibrary(d, false)}>To Library</button>
          <button className="btn grow" disabled={!d} onClick={() => d && sendToLibrary(d, true)}>To Video Editor</button>
        </div>
      </div>
      {(engines.rb || engines.ip) && <div className="faint" style={{ fontSize: 11.5, marginBottom: 6 }}>Engine used - {engines.rb ? `remove background: ${engines.rb}` : ''}{engines.rb && engines.ip ? ' · ' : ''}{engines.ip ? `inpaint: ${engines.ip}` : ''}</div>}
      {busy && <div className="muted row" style={{ fontSize: 12 }}><Loader2 size={13} className="spin" />{msg || `${busy}…`}</div>}
      <JobBar job={job} error={err} />
    </div>
  )
}

export function AssetsPanel() {
  const [tab, setTab] = useState<'lib' | 'bg' | 'st'>('lib')
  const [media, setMedia] = useState<MediaRow[]>([])
  const [cat, setCat] = useState<CatalogLite | null | undefined>(undefined)
  const [q, setQ] = useState('')
  const reload = () => { void fetchImages(q).then(setMedia) }
  useEffect(() => { reload() /* eslint-disable-next-line */ }, [tab === 'lib'])
  useEffect(() => { void fetchCatalog().then(setCat) }, [])
  return (
    <div className="ie-assets">
      <div className="row" style={{ marginBottom: 6 }}>
        <Seg value={tab} onChange={setTab} options={[['lib', 'Library'], ['bg', 'Backgrounds'], ['st', 'Stickers']]} />
        <div className="grow" />
        {tab === 'lib' && <button className="btn ghost sm" onClick={reload}>Refresh</button>}
      </div>
      {tab === 'lib' && (
        <>
          <input className="input" placeholder="Search images…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && reload()} style={{ marginBottom: 6 }} />
          {!media.length && <div className="faint" style={{ padding: 12, textAlign: 'center' }}>No images in the library yet</div>}
          <div className="agrid">
            {media.map((m) => (
              <div key={m.id} className="acell" title={`${m.name}\nClick to insert as a new layer`} onClick={() => void insertImageUrl(m.url, m.name.replace(/\.[^.]+$/, ''))}>
                <img src={m.size < 1.5e6 ? m.url : m.thumb} loading="lazy" /><span>{m.name}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {tab !== 'lib' && cat === undefined && <div className="faint">Loading…</div>}
      {tab !== 'lib' && cat === null && <div className="faint" style={{ padding: 12, textAlign: 'center' }}>The asset catalog is unavailable (the backend does not provide /api/assets/catalog)</div>}
      {tab === 'bg' && cat && (
        <div className="agrid">
          {cat.backgrounds.map((b) => (
            <div key={b.id} className="acell" title="Click to insert as the background (bottom layer, fills the canvas)"
              onClick={() => {
                const d = curDoc()
                if (b.url) void insertImageUrl(b.url, b.name, true, true)
                else placeCanvas(cssToCanvas(b.css, d?.w ?? 1920, d?.h ?? 1080), b.name, undefined, true)
              }}>
              {b.url ? <img src={b.url} loading="lazy" /> : <div className="sw" style={{ background: b.css }} />}<span>{b.name}</span>
            </div>
          ))}
          {!cat.backgrounds.length && <div className="faint">No background assets</div>}
        </div>
      )}
      {tab === 'st' && cat && (
        <div className="agrid">
          {cat.stickers.map((s) => (
            <div key={s.id} className="acell sticker" title="Click to insert the sticker" onClick={() => void insertImageUrl(s.url, s.name)}>
              <img src={s.url} loading="lazy" /><span>{s.name}</span>
            </div>
          ))}
          {!cat.stickers.length && <div className="faint">No stickers</div>}
        </div>
      )}
    </div>
  )
}
