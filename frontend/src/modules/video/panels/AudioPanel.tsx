import MusicPanel from '../../ai/MusicPanel'
import TtsPanel from '../../ai/TtsPanel'
import { Section } from '../ui'
import MediaPanel from './MediaPanel'
import { usePanelProps } from './panelProps'

export default function AudioPanel() {
  const pp = usePanelProps()
  return (
    <div className="ve-panel-body" style={{ padding: 0 }}>
      <Section title="Audio library">
        <MediaPanel kinds={['audio']} />
      </Section>
      <Section title="Music generation (AI)" defaultOpen={false}>
        <MusicPanel {...pp} />
      </Section>
      <Section title="Text to speech (TTS)" defaultOpen={false}>
        <TtsPanel {...pp} />
      </Section>
    </div>
  )
}
