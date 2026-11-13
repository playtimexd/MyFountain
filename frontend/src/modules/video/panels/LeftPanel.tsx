import { useEffect, useState } from 'react'
import { Film, Music, Type, Smile, Shuffle, Palette, Sparkles } from 'lucide-react'
import AiToolsPanel from '../../ai/AiToolsPanel'
import SearchPanel from '../../ai/SearchPanel'
import AgentPanel from '../../ai/AgentPanel'
import MediaPanel from './MediaPanel'
import AudioPanel from './AudioPanel'
import TextPanel from './TextPanel'
import { StickerPanel, TransitionPanel, FilterPanel } from './EffectPanels'
import { usePanelProps } from './panelProps'
import { useAiFocus } from '../../ai/focus'

const TABS = [
  { id: 'media', label: 'Media', icon: Film },
  { id: 'audio', label: 'Audio', icon: Music },
  { id: 'text', label: 'Text', icon: Type },
  { id: 'sticker', label: 'Stickers', icon: Smile },
  { id: 'transition', label: 'Transitions', icon: Shuffle },
  { id: 'filter', label: 'Filters', icon: Palette },
  { id: 'ai', label: 'AI', icon: Sparkles },
] as const
type TabId = (typeof TABS)[number]['id']

const AI_SUB = [{ id: 'tools', label: 'Tools' }, { id: 'agent', label: 'Assistant' }, { id: 'search', label: 'Search' }] as const
type AiSub = (typeof AI_SUB)[number]['id']

function AiPanels() {
  const pp = usePanelProps()
  const [sub, setSub] = useState<AiSub>(() => { const v = localStorage.getItem('cutlab.video.aisub') as AiSub; return AI_SUB.some((s) => s.id === v) ? v : 'tools' })
  const pick = (s: AiSub) => { setSub(s); try { localStorage.setItem('cutlab.video.aisub', s) } catch { /* ignore */ } }
  const nonce = useAiFocus((s) => s.nonce)
  useEffect(() => { if (nonce) setSub('tools') }, [nonce])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ padding: '10px 12px 0', flex: 'none' }}>
        <div className="seg-tabs" style={{ width: '100%' }}>
          {AI_SUB.map((s) => <button key={s.id} className={sub === s.id ? 'on' : ''} style={{ flex: 1 }} onClick={() => pick(s.id)}>{s.label}</button>)}
        </div>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        {sub === 'tools' && <AiToolsPanel {...pp} hideTitle />}
        {sub === 'agent' && <AgentPanel {...pp} />}
        {sub === 'search' && <div style={{ height: '100%', overflow: 'auto' }}><SearchPanel {...pp} /></div>}
      </div>
    </div>
  )
}

export default function LeftPanel() {
  const [tab, setTab] = useState<TabId>(() => (localStorage.getItem('cutlab.video.tab') as TabId) || 'media')
  const pick = (t: TabId) => { setTab(t); try { localStorage.setItem('cutlab.video.tab', t) } catch { /* ignore */ } }
  // an AI action from the inspector / clip menu switches to the AI tab
  const nonce = useAiFocus((s) => s.nonce)
  useEffect(() => { if (nonce) pick('ai') }, [nonce])
  return (
    <div className="ve-left">
      <div className="ve-tabs">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? 'on' : ''} onClick={() => pick(t.id)}><t.icon size={16} /><span>{t.label}</span></button>
        ))}
      </div>
      <div className="ve-left-body">
        {tab === 'media' && <MediaPanel />}
        {tab === 'audio' && <AudioPanel />}
        {tab === 'text' && <TextPanel />}
        {tab === 'sticker' && <StickerPanel />}
        {tab === 'transition' && <TransitionPanel />}
        {tab === 'filter' && <FilterPanel />}
        {tab === 'ai' && <AiPanels />}
      </div>
    </div>
  )
}
