import { useState } from 'react'
import { Bot, FolderSearch, History, Layers, Mic, Music } from 'lucide-react'
import ActivityHistory from '../../shared/projects/ActivityHistory'
import type { MediaRow } from '../../types/api'
import { SideNav } from '../../shared/ui'
import MusicPanel from './MusicPanel'
import SearchPanel from './SearchPanel'
import AgentPanel from './AgentPanel'
import TtsPanel from './TtsPanel'
import BatchPanel from './BatchPanel'
import MediaPicker from './MediaPicker'

// Single-file tools (upscale, watermark, subtitles, smart cut, background removal) live inside the
// Video Editor (inspector / clip right-click → AI) and the Image Editor (AI menu). This page keeps
// the generators, library-wide tools and a batch mode for many files at once.
const NAV = [
  { id: 'music', label: 'AI Music', desc: 'Generate background music locally', icon: Music },
  { id: 'tts', label: 'Voice-over', desc: 'Turn text into speech', icon: Mic },
  { id: 'batch', label: 'Batch', desc: 'Upscale, de-watermark or cut out many files', icon: Layers },
  { id: 'search', label: 'Media Search', desc: 'Search local files by content', icon: FolderSearch },
  { id: 'agent', label: 'Assistant', desc: 'Chat-based analysis and suggestions', icon: Bot },
  { id: 'history', label: 'History', desc: 'Past AI jobs and their results', icon: History },
] as const
type NavId = (typeof NAV)[number]['id']

/** Standalone "AI" page: no host project, so applyOps is undefined (apply buttons hide); results land in the library. */
export default function AiHub() {
  const [tab, setTab] = useState<NavId>(() => {
    try { const t = localStorage.getItem('cutlab.ai.tab'); if (t === 'tools') return 'batch'; return NAV.some((n) => n.id === t) ? (t as NavId) : 'music' } catch { return 'music' }
  })
  const [target, setTarget] = useState<MediaRow | null>(null)
  const pick = (t: NavId) => { setTab(t); try { localStorage.setItem('cutlab.ai.tab', t) } catch { /* ignore */ } }
  const common = { onAddAsset: () => { /* results are already in the media library */ }, standalone: true as const, selection: { clipIds: [], mediaIds: target ? [target.id] : [] } }
  const show = (id: NavId) => ({ display: tab === id ? 'flex' : 'none', flexDirection: 'column' as const, height: '100%', minHeight: 0 })

  return (
    <div style={{ display: 'flex', width: '100%', height: '100%', minHeight: 0 }}>
      <SideNav items={NAV} value={tab} onChange={pick}
        foot={<>All models run on this machine. Results are saved to the Library.<br />Single-file tools: Video Editor → right-click a clip, Image Editor → AI menu.</>} />
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {tab === 'agent' && (
          <div style={{ padding: '12px 16px 0', maxWidth: 820, width: '100%', margin: '0 auto' }}>
            <MediaPicker value={target?.id || null} onChange={setTarget} label="Media to discuss (optional)" />
          </div>
        )}
        <div style={{ flex: 1, minHeight: 0, maxWidth: 820, width: '100%', margin: '0 auto' }}>
          <div style={show('music')}><MusicPanel {...common} /></div>
          <div style={show('tts')}><TtsPanel {...common} /></div>
          <div style={show('batch')}><BatchPanel /></div>
          <div style={show('search')}><SearchPanel {...common} /></div>
          <div style={show('agent')}><AgentPanel {...common} /></div>
          <div style={{ ...show('history'), padding: 14 }}>{tab === 'history' && <ActivityHistory areas={['ai']} title="AI history" empty="Finished AI jobs (music, voice-over, batch upscale, subtitles …) appear here with their results, also after a restart." />}</div>
        </div>
      </div>
    </div>
  )
}
