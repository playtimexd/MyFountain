import { Suspense, lazy, useEffect, useState } from 'react'
import { Box, Clapperboard, FolderOpen, Image as ImageIcon, PersonStanding, Settings, Sparkles, SplitSquareHorizontal } from 'lucide-react'
import { api } from './api/client'
import { on } from './shared/bus'
import SettingsDialog from './shared/SettingsDialog'
import { Loading } from './shared/ui'
import './App.css'

const VideoEditor = lazy(() => import('./modules/video/VideoEditor'))
const ImageEditor = lazy(() => import('./modules/image/ImageEditor'))
const Library = lazy(() => import('./modules/library/Library'))
const VideoSegment = lazy(() => import('./modules/segment/VideoSegment'))
const AiHub = lazy(() => import('./modules/ai/AiHub'))
const AnimHub = lazy(() => import('./modules/anim/AnimHub'))
const ModelsHub = lazy(() => import('./modules/models/ModelsHub'))

const TABS = [
  { id: 'video', label: 'Video Editor', icon: Clapperboard },
  { id: 'image', label: 'Image Editor', icon: ImageIcon },
  { id: 'library', label: 'Library', icon: FolderOpen },
  { id: 'download', label: 'Video Segment', icon: SplitSquareHorizontal },
  { id: 'models', label: '3D Models', icon: Box },
  { id: 'anim', label: 'Animation', icon: PersonStanding },
  { id: 'ai', label: 'AI Studio', icon: Sparkles },
] as const
type TabId = (typeof TABS)[number]['id']

export default function App() {
  const [tab, setTab] = useState<TabId>(() => (localStorage.getItem('cutlab.tab') as TabId) || 'video')
  const [online, setOnline] = useState<boolean | null>(null)
  const [settings, setSettings] = useState(false)
  useEffect(() => { localStorage.setItem('cutlab.tab', tab) }, [tab])
  useEffect(() => {
    const ping = () => api.get('/api/health').then(() => setOnline(true)).catch(() => setOnline(false))
    ping(); const t = setInterval(ping, 5000); return () => clearInterval(t)
  }, [])

  // All modules stay mounted once opened so editor state survives tab switches.
  const [opened, setOpened] = useState<Set<TabId>>(new Set([tab]))
  useEffect(() => { setOpened((s) => (s.has(tab) ? s : new Set(s).add(tab))) }, [tab])
  useEffect(() => on('cutlab:goto', (d) => setTab(d.tab)), [])

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><span className="logo" /><span className="brand-name">CutLab</span></div>
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? 'on' : ''}`} onClick={() => setTab(t.id)} title={t.label} aria-current={tab === t.id ? 'page' : undefined}>
              <t.icon size={15} /><span className="tab-label">{t.label}</span>
            </button>
          ))}
        </nav>
        <div className="grow" />
        <span className={`engine ${online === false ? 'bad' : ''}`} title={online ? 'Local engine connected' : online === false ? 'Engine offline: AI, import and export are unavailable' : 'Connecting to the local engine…'}>
          <span className={`dot ${online ? 'ok' : online === false ? 'bad' : ''}`} />
          <span className="engine-label">{online ? 'Engine connected' : online === false ? 'Engine offline' : 'Connecting…'}</span>
        </span>
        <button className="btn ghost icon" title="Settings" onClick={() => setSettings(true)}><Settings size={16} /></button>
      </header>
      <main className="stage">
        <Suspense fallback={<Loading />}>
          {TABS.filter((t) => opened.has(t.id)).map((t) => (
            <section key={t.id} className="page" style={{ display: tab === t.id ? 'flex' : 'none' }}>
              {t.id === 'video' && <VideoEditor />}
              {t.id === 'image' && <ImageEditor />}
              {t.id === 'library' && <Library />}
              {t.id === 'download' && <VideoSegment onGoLibrary={() => setTab('library')} />}
              {t.id === 'models' && <ModelsHub />}
              {t.id === 'anim' && <AnimHub />}
              {t.id === 'ai' && <AiHub />}
            </section>
          ))}
        </Suspense>
      </main>
      {settings && <SettingsDialog onClose={() => setSettings(false)} />}
    </div>
  )
}
