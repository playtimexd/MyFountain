import { useCallback, useEffect, useState } from 'react'
import { Box, Film, Library, PersonStanding, Sparkles, Wand, Wand2 } from 'lucide-react'
import { on } from '../../shared/bus'
import { SideNav } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import { useAnimData } from './animApi'
import { Note } from './animUi'
import LibrarySection from './LibrarySection'
import RigsSection from './RigsSection'
import RetargetSection from './RetargetSection'
import FromVideoSection from './FromVideoSection'
import VideoGenPanel from './providers/VideoGenPanel'
import AnimProjects from './AnimProjects'
import AnimWizard from './AnimWizard'

const NAV = [
  { id: 'make', label: 'Make animation', desc: 'Video + character → game animation (4 steps)', icon: Wand },
  { id: 'library', label: 'Library', desc: 'Clips, characters, search', icon: Library },
  { id: 'rigs', label: 'Rigs', desc: 'Import characters · bone map', icon: PersonStanding },
  { id: 'retarget', label: 'Retarget & Export', desc: 'Motion → character → GLB / FBX', icon: Wand2 },
  { id: 'video', label: 'From Video', desc: 'Video → motion capture', icon: Film },
  { id: 'generate', label: 'Reference video', desc: 'Generate a video to capture motion from', icon: Sparkles },
] as const
type NavId = (typeof NAV)[number]['id']

const pref = (k: string, d: string) => { try { return localStorage.getItem(k) || d } catch { return d } }
const store = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

export default function AnimHub() {
  const data = useAnimData()
  const [tab, setTab] = useState<NavId>(() => { const t = pref('cutlab.anim.tab2', 'make') as NavId; return NAV.some((n) => n.id === t) ? t : 'make' })
  const [clipId, setClipId] = useState('')
  const [rigId, setRigId] = useState('')
  const [mapRig, setMapRig] = useState<string | null>(null)
  const [video, setVideo] = useState<MediaRow | null>(null)
  const [range, setRange] = useState<{ start?: number; end?: number }>({})
  const go = useCallback((t: NavId) => { setTab(t); store('cutlab.anim.tab2', t) }, [])

  // a library video sent here as a motion-capture source (Library -> "Extract motion")
  // a character handed over from the 3D Models workspace
  useEffect(() => on('cutlab:to-anim-rig', (d) => { data.reload(); setRigId(d.rigId); setMapRig(d.rigId); go('rigs') }), [go, data.reload])
  useEffect(() => on('cutlab:to-anim-clip', (d) => { data.reload(); setClipId(d.clipId); go(d.action === 'retarget' ? 'retarget' : 'library') }), [go, data.reload])
  useEffect(() => on('cutlab:to-mocap', (d) => { setVideo(d.media); setRange({ start: d.start, end: d.end }); go('video') }), [go])
  // keep a sensible default selection
  useEffect(() => { if (!rigId && data.rigs.length) setRigId(data.rigs[0].id) }, [data.rigs, rigId])
  useEffect(() => { if (!clipId) { const s = data.clips.find((c) => c.kind === 'source'); if (s) setClipId(s.id) } }, [data.clips, clipId])

  const show = (id: NavId) => ({ display: tab === id ? 'flex' : 'none', flexDirection: 'column' as const, height: '100%', minHeight: 0 })
  const bl = data.status?.blender

  return (
    <div style={{ display: 'flex', width: '100%', height: '100%', minHeight: 0 }}>
      <SideNav items={NAV} value={tab} onChange={go} foot={<>
        <AnimProjects state={{ tab, clipId, rigId, mapRig, video, range }} set={{ go: (t) => go(NAV.some((n) => n.id === t) ? (t as NavId) : 'library'), setClipId, setRigId, setMapRig, setVideo, setRange }} rigs={data.rigs} clips={data.clips} />
        <div className="row" style={{ gap: 6, marginTop: 6 }}>
          <span className={`dot ${bl?.available ? 'ok' : 'bad'}`} />
          {bl ? (bl.available ? <span>Blender {bl.version} ready</span> : <span style={{ color: 'var(--warn)' }}>{bl.note}</span>) : <span style={{ color: 'var(--warn)' }}>Engine offline or the animation router is not loaded (restart the engine).</span>}
        </div>
      </>} />
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {data.err && <Note tone="warn">Animation API: {data.err}</Note>}
        <div style={{ flex: 1, minHeight: 0 }}>
          <div style={show('make')}>
            {tab === 'make' && <AnimWizard rigs={data.rigs} reloadRigs={data.reload} onAdvanced={() => go('retarget')} />}
          </div>
          <div style={show('library')}>
            <LibrarySection rigs={data.rigs} clips={data.clips} reload={data.reload}
              onRetarget={(id) => { setClipId(id); go('retarget') }} onOpenRig={(id) => { setMapRig(id); go('rigs') }} />
          </div>
          <div style={show('rigs')}>
            <RigsSection rigs={data.rigs} clips={data.clips} selectedId={mapRig} onSelect={(id) => { setMapRig(id); if (id) setRigId(id) }} reload={data.reload} />
          </div>
          <div style={show('retarget')}>
            <RetargetSection rigs={data.rigs} clips={data.clips} status={data.status} reload={data.reload} reloadStatus={data.reloadStatus}
              clipId={clipId} rigId={rigId} setClipId={setClipId} setRigId={setRigId} onEditMap={(id) => { setMapRig(id); go('rigs') }} />
          </div>
          <div style={show('video')}>
            <FromVideoSection rigs={data.rigs} media={video} setMedia={setVideo} range={range} setRange={setRange} reload={data.reload}
              onOpenClip={(id) => { setClipId(id); go('library') }} onRetarget={(id) => { setClipId(id); go('retarget') }} />
          </div>
          <div style={show('generate')}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))', gap: 14, padding: 14, overflow: 'auto', height: '100%', alignContent: 'start' }}>
              <div className="panel" style={{ minWidth: 0 }}>
                <div className="panel-title"><Box size={14} />Need a character?</div>
                <div style={{ padding: 12, fontSize: 12 }} className="muted">
                  3D characters are generated, imported and groomed in the <b>3D Models</b> workspace. Use “Send to Animation” there and the character appears in Rigs.
                  <div style={{ marginTop: 8 }}><button className="btn sm" onClick={() => window.dispatchEvent(new CustomEvent('cutlab:goto', { detail: { tab: 'models' } }))}>Open 3D Models</button></div>
                </div>
              </div>
              <div className="panel" style={{ minWidth: 0 }}>
                <div className="panel-title"><Film size={14} />Reference video → motion capture</div>
                <VideoGenPanel onAddMedia={(m) => { setVideo(m); setRange({}); go('video') }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
