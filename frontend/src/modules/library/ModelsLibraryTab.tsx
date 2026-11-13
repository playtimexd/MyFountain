import { useState } from 'react'
import { emit } from '../../shared/bus'
import ModelLibrary from '../models/ModelLibrary'
import LocalAssets from '../models/LocalAssets'

/** 3D models as a category of the main Library (same view as 3D Models → Model library). */
export default function ModelsLibraryTab() {
  const [view, setView] = useState<'lib' | 'local'>('lib')
  return (
    <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div className="seg-tabs" style={{ margin: '8px 12px 0', alignSelf: 'flex-start' }}>
        <button className={view === 'lib' ? 'on' : ''} onClick={() => setView('lib')}>Model library</button>
        <button className={view === 'local' ? 'on' : ''} onClick={() => setView('local')}>3D files on this PC</button>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>{view === 'lib' ? <ModelLibrary reloadKey={0} onFur={(source) => emit('cutlab:to-fur', { source })} /> : <LocalAssets />}</div>
    </div>
  )
}
