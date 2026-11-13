import { useEffect, useState } from 'react'
import { Copy, Download, RefreshCw } from 'lucide-react'
import { api } from '../../api/client'
import { copyText, downloadText } from './api'
import { toast } from './common'

export default function Credits({ ids, clear }: { ids: string[] | null; clear: () => void }) {
  const [md, setMd] = useState('')
  const [count, setCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const build = async () => {
    setBusy(true)
    try { const r = await api.post<{ markdown: string; count: number }>('/api/hub/credits', { asset_ids: ids ?? [] }); setMd(r.markdown); setCount(r.count) }
    catch (e: any) { toast(e.message) } finally { setBusy(false) }
  }
  useEffect(() => { build() }, [ids]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="hub-scroll" style={{ maxWidth: 1000 }}>
      <div className="hub-section">
        <h3>Credits / attribution export</h3>
        <p className="hub-note" style={{ marginTop: 0 }}>
          {ids ? <>Credits for the {ids.length} selected asset(s). <button className="btn sm ghost" onClick={clear}>Use the whole hub instead</button></>
            : <>Credits for every asset in the hub ({count}). Select assets in Browse (tick boxes) and choose "Credits for selection" to export only what your game uses.</>}
          {' '}Assets that require attribution (CC-BY…) are listed first; ship this file with your game.
        </p>
        <div className="row" style={{ marginBottom: 8 }}>
          <button className="btn sm" onClick={build} disabled={busy}><RefreshCw size={12} />Rebuild</button>
          <button className="btn sm" onClick={async () => toast((await copyText(md)) ? 'CREDITS.md copied' : 'Could not copy')}><Copy size={12} />Copy</button>
          <button className="btn sm primary" onClick={() => downloadText('CREDITS.md', md)}><Download size={12} />Download CREDITS.md</button>
        </div>
        <textarea className="textarea" style={{ minHeight: 420, fontFamily: 'var(--mono)', fontSize: 12 }} value={md} onChange={(e) => setMd(e.target.value)} />
      </div>
    </div>
  )
}
