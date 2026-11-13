import { useEffect, useState } from 'react'
import { Link2 } from 'lucide-react'
import { useBridge } from './bridge'

const ACTIVE_MS = 10000

export default function BridgeChip() {
  const { lastCmd, pulse, lastInfo, online } = useBridge()
  const [now, setNow] = useState(() => Date.now())
  const live = now - lastCmd < ACTIVE_MS
  useEffect(() => {
    if (!live && Date.now() - lastCmd >= ACTIVE_MS) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [live, lastCmd])
  useEffect(() => { setNow(Date.now()) }, [lastCmd, pulse])
  const pulsing = Date.now() - pulse < 900
  const tip = [
    'AI link: Claude Code / Codex can read and edit this timeline through the CutLab MCP server.',
    live ? 'A command was received in the last 10 s.' : online ? 'Connected and waiting for commands.' : 'Engine bridge not reachable.',
    lastInfo ? `Last: ${lastInfo}` : '',
  ].filter(Boolean).join('\n')
  return (
    <span className={`ve-ailink ${live ? 'live' : ''} ${pulsing ? 'pulse' : ''} ${online ? '' : 'off'}`} title={tip}>
      <Link2 size={12} />AI link
    </span>
  )
}
