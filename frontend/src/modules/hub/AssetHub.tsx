// Asset Hub: license-aware database of free game assets (official APIs + manual pack imports).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmtSize } from '../../api/client'
import type { HubStats, SourceInfo } from './api'
import Browse from './Browse'
import Online from './Online'
import Harvest from './Harvest'
import ImportPack from './ImportPack'
import type { ImportPreset } from './ImportPack'
import Sources from './Sources'
import Credits from './Credits'
import './hub.css'

type Tab = 'browse' | 'online' | 'harvest' | 'import' | 'sources' | 'credits'
const TABS: [Tab, string][] = [['browse', 'Browse'], ['online', 'Online search'], ['harvest', 'Harvest'], ['import', 'Import pack'], ['sources', 'Sources & keys'], ['credits', 'Credits']]

export default function AssetHub() {
  const [tab, setTab] = useState<Tab>('browse')
  const [sources, setSources] = useState<SourceInfo[]>([])
  const [stats, setStats] = useState<HubStats | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [creditIds, setCreditIds] = useState<string[] | null>(null)
  const [preset, setPreset] = useState<ImportPreset | null>(null)

  const loadMeta = useCallback(() => {
    api.get<SourceInfo[]>('/api/hub/sources').then((s) => { setSources(s); setErr(null) })
      .catch((e) => setErr(`Asset Hub engine API not reachable (${e.message}). The engine may need a restart to load the hub router.`))
    api.get<HubStats>('/api/hub/stats').then(setStats).catch(() => {})
  }, [])
  useEffect(() => { loadMeta() }, [loadMeta, refresh])
  const changed = useCallback(() => setRefresh((r) => r + 1), [])
  const names = useMemo(() => Object.fromEntries(sources.map((s) => [s.id, s.name])), [sources])

  return (
    <div className="hub">
      <div className="hub-top">
        <div className="seg-tabs">
          {TABS.map(([id, label]) => <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}
        </div>
        {stats && (
          <span className="faint" style={{ fontSize: 12 }}>
            {stats.total} assets · {fmtSize(stats.bytes)} · {stats.by_license_class['commercial-safe'] ?? 0} commercial-safe · {stats.by_license_class.attribution ?? 0} need credit
            {stats.rigged ? ` · ${stats.rigged} rigged` : ''}{stats.animated ? ` · ${stats.animated} animated` : ''}
          </span>
        )}
        {err && <span className="err" style={{ marginTop: 0 }}>{err}</span>}
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {tab === 'browse' && <Browse sourceNames={names} refreshKey={refresh} onCredits={(ids) => { setCreditIds(ids); setTab('credits') }} />}
        {tab === 'online' && <Online sources={sources} onChanged={changed}
          onImport={(source, license, packName) => { setPreset({ source, license, packName, nonce: Date.now() }); setTab('import') }} />}
        {tab === 'harvest' && <Harvest sources={sources} onChanged={changed} />}
        {tab === 'import' && <ImportPack preset={preset} onChanged={changed} />}
        {tab === 'sources' && <Sources sources={sources} reload={changed} />}
        {tab === 'credits' && <Credits ids={creditIds} clear={() => setCreditIds(null)} />}
      </div>
    </div>
  )
}
