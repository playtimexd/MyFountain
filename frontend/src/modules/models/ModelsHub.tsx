import { useCallback, useEffect, useState } from 'react'
import { Box, HardDrive, Library, PawPrint, Sparkles } from 'lucide-react'
import { on } from '../../shared/bus'
import { SideNav } from '../../shared/ui'
import Gen3DPanel from '../anim/providers/Gen3DPanel'
import FurSection from '../anim/FurSection'
import ModelLibrary from './ModelLibrary'
import LocalAssets from './LocalAssets'
import ModelProjects from './ModelProjects'

/** 3D Models workspace: generate / import models, preview them, groom fur, and hand characters to Animation. */
const NAV = [
  { id: 'library', label: 'Model library', desc: 'Characters, generated & imported models', icon: Library },
  { id: 'local', label: 'On this PC', desc: 'Index your projects, Mixamo & downloads', icon: HardDrive },
  { id: 'generate', label: 'Generate', desc: 'Text / image → 3D (Tripo, Meshy)', icon: Sparkles },
  { id: 'fur', label: 'Fur', desc: 'Blender groom · real-time shell fur', icon: PawPrint },
] as const
type NavId = (typeof NAV)[number]['id']

const pref = (k: string, d: string) => { try { return localStorage.getItem(k) || d } catch { return d } }
const store = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

export default function ModelsHub() {
  const [tab, setTab] = useState<NavId>(() => { const t = pref('cutlab.models.tab', 'library') as NavId; return NAV.some((n) => n.id === t) ? t : 'library' })
  const [reloadKey, setReloadKey] = useState(0)
  const [furSource, setFurSource] = useState<string | undefined>()
  const go = useCallback((t: NavId) => { setTab(t); store('cutlab.models.tab', t) }, [])
  const openFur = useCallback((source: string) => { setFurSource(source); go('fur') }, [go])
  useEffect(() => on('cutlab:to-fur', (d) => openFur(d.source)), [openFur])

  const show = (id: NavId) => ({ display: tab === id ? 'flex' : 'none', flexDirection: 'column' as const, height: '100%', minHeight: 0 })
  return (
    <div style={{ display: 'flex', width: '100%', height: '100%', minHeight: 0 }}>
      <SideNav items={NAV} value={tab} onChange={go} foot={<>
        <ModelProjects tab={tab} source={furSource} go={(t) => go(NAV.some((n) => n.id === t) ? (t as NavId) : 'library')} setSource={setFurSource} />
        Characters you send to Animation get bone mapping, motion capture and retargeting there.</>} />
      <div style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
        <div style={show('library')}><ModelLibrary reloadKey={reloadKey} onFur={openFur} /></div>
        <div style={show('local')}>{tab === 'local' && <LocalAssets />}</div>
        <div style={show('generate')}>
          <div style={{ padding: 14, overflow: 'auto', height: '100%' }}>
            <div className="panel" style={{ maxWidth: 760 }}>
              <div className="panel-title"><Box size={14} />Text / image → 3D model</div>
              <Gen3DPanel onAddRig={() => { setReloadKey((k) => k + 1) }} />
            </div>
          </div>
        </div>
        <div style={show('fur')}>{tab === 'fur' && <FurSection preferSource={furSource} />}</div>
      </div>
    </div>
  )
}
