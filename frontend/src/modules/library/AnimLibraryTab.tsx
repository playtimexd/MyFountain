import { useState } from 'react'
import { emit } from '../../shared/bus'
import LocalAssets from '../models/LocalAssets'
import { useAnimData } from '../anim/animApi'
import LibrarySection from '../anim/LibrarySection'
import { Note } from '../anim/animUi'

/** Animation clips + characters as a category of the main Library; editing actions hand over to the Animation workspace. */
export default function AnimLibraryTab() {
  const data = useAnimData()
  const [view, setView] = useState<'lib' | 'local'>('lib')
  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div className="seg-tabs" style={{ margin: '8px 12px 0', alignSelf: 'flex-start' }}>
        <button className={view === 'lib' ? 'on' : ''} onClick={() => setView('lib')}>Animation library</button>
        <button className={view === 'local' ? 'on' : ''} onClick={() => setView('local')}>Animation files on this PC</button>
      </div>
      {view === 'local' ? <div style={{ flex: 1, minHeight: 0 }}><LocalAssets kinds={['animation', 'animated-character', 'animated-model']} title="Animation files on this PC" /></div> : <>
      {data.err && <Note tone="warn">Animation library: {data.err}</Note>}
      <LibrarySection rigs={data.rigs} clips={data.clips} reload={data.reload}
        onRetarget={(id) => emit('cutlab:to-anim-clip', { clipId: id, action: 'retarget' })}
        onOpenRig={(id) => emit('cutlab:to-anim-rig', { rigId: id })} />
      </>}
    </div>
  )
}
