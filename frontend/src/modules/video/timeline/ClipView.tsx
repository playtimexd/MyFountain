import { memo, useEffect, useRef, useState } from 'react'
import { Volume2, VolumeOff } from 'lucide-react'
import type { Clip, TrackKind } from '../../../types/project'
import { clipDuration } from '../../../types/project'
import type { MediaRow } from '../../../types/api'
import { api, thumbUrl } from '../../../api/client'
import { stickerUrl } from '../data'

const peaksCache = new Map<string, Promise<number[]>>()
function loadPeaks(id: string) {
  let p = peaksCache.get(id)
  if (!p) {
    p = api.get<{ peaks: number[] }>(`/api/media/${id}/waveform`).then((r) => r.peaks || []).catch(() => [])
    peaksCache.set(id, p)
  }
  return p
}

function Waveform({ clip, media, width, height }: { clip: Clip; media?: MediaRow; width: number; height: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [peaks, setPeaks] = useState<number[] | null>(null)
  useEffect(() => { if (clip.assetId) loadPeaks(clip.assetId).then(setPeaks) }, [clip.assetId])
  useEffect(() => {
    const cv = ref.current
    if (!cv || !peaks || !peaks.length) return
    const W = Math.max(8, Math.min(3000, Math.round(width))), H = Math.max(8, Math.round(height))
    cv.width = W; cv.height = H
    const ctx = cv.getContext('2d')!
    ctx.clearRect(0, 0, W, H)
    const md = media?.duration || clip.out
    const i0 = (clip.in / md) * peaks.length, i1 = (clip.out / md) * peaks.length
    ctx.fillStyle = 'rgba(255,255,255,.75)'
    const mid = H / 2
    for (let x = 0; x < W; x++) {
      const a = Math.floor(i0 + ((i1 - i0) * x) / W), b = Math.max(a + 1, Math.floor(i0 + ((i1 - i0) * (x + 1)) / W))
      let m = 0
      for (let i = a; i < b && i < peaks.length; i++) if (peaks[i] > m) m = peaks[i]
      const h = Math.max(1, Math.min(1, m * 1.6) * (H - 6))
      ctx.fillRect(x, mid - h / 2, 1, h)
    }
  }, [peaks, clip.in, clip.out, width, height, media?.duration])
  return <canvas ref={ref} className="ve-wave" />
}

export interface ClipViewProps {
  clip: Clip
  zoom: number
  selected: boolean
  locked: boolean
  trackKind: TrackKind
  height: number
  media?: MediaRow
  onDown: (e: React.PointerEvent, clip: Clip, mode: 'move' | 'trimL' | 'trimR') => void
  onSplit?: (clip: Clip) => void
}

function ClipViewImpl({ clip, zoom, selected, locked, height, media, onDown }: ClipViewProps) {
  const dur = clipDuration(clip)
  const left = clip.start * zoom, width = Math.max(3, dur * zoom)
  const cls = `ve-clip t-${clip.type} ${selected ? 'sel' : ''} ${locked ? 'locked' : ''}`
  const style: React.CSSProperties = { left, width, height: height - 4 }
  if ((clip.type === 'video' || clip.type === 'image') && clip.assetId) {
    const aspect = media && media.height ? media.width / media.height : 16 / 9
    const tile = Math.max(24, Math.round((height - 4) * aspect))
    style.backgroundImage = `url(${thumbUrl(clip.assetId)})`
    style.backgroundSize = `${tile}px 100%`
    style.backgroundRepeat = 'repeat-x'
  } else if (clip.type === 'sticker' && clip.assetId) {
    style.backgroundImage = `url(${stickerUrl(clip.assetId)})`
    style.backgroundSize = 'auto 80%'
    style.backgroundRepeat = 'no-repeat'
    style.backgroundPosition = '30px center'
  }
  const label = clip.type === 'text' ? clip.text?.content ?? 'Text' : clip.name || media?.name || clip.type
  const fi = clip.fadeIn * zoom, fo = clip.fadeOut * zoom
  return (
    <div className={cls} style={style} data-clip={clip.id} onPointerDown={(e) => onDown(e, clip, 'move')}>
      {clip.type === 'audio' && <Waveform clip={clip} media={media} width={width} height={height - 4} />}
      {fi > 2 && <i className="ve-fade in" style={{ width: fi }} />}
      {fo > 2 && <i className="ve-fade out" style={{ width: fo }} />}
      <span className="ve-clip-label">
        {clip.type === 'audio' && <Volume2 size={10} />}{clip.audioDetached && <span title="Audio split onto its own clip" style={{ display: 'inline-flex' }}><VolumeOff size={10} /></span>}{label}
        {clip.speed !== 1 && <b className="ve-badge">{+clip.speed.toFixed(2)}x</b>}
        {clip.transitionIn && <b className="ve-badge tr">Transition</b>}
      </span>
      <i className="ve-trim l" onPointerDown={(e) => { e.stopPropagation(); onDown(e, clip, 'trimL') }} />
      <i className="ve-trim r" onPointerDown={(e) => { e.stopPropagation(); onDown(e, clip, 'trimR') }} />
    </div>
  )
}

export const ClipView = memo(ClipViewImpl)
