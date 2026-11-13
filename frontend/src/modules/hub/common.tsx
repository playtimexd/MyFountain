import { useCallback, useEffect, useRef, useState } from 'react'
import { Box, Film, Image as ImageIcon, Music, PersonStanding, Play, Type, Sun, Layers } from 'lucide-react'
import { api } from '../../api/client'
import { emit } from '../../shared/bus'
import type { HubAsset, LicenseClass } from './api'
import { AUDIO_KINDS, copyText, hubFile, kindLabel, mediaFor } from './api'

const CLASS_LABEL: Record<LicenseClass, string> = { 'commercial-safe': 'Commercial-safe', attribution: 'Credit required', 'non-commercial': 'Non-commercial', restricted: 'Restricted' }

export function LicenseBadge({ license, cls, title }: { license: string; cls: LicenseClass; title?: string }) {
  const short = license.startsWith('custom:') ? license.slice(7) : license
  return <span className={`badge lic-${cls}`} title={title || `${CLASS_LABEL[cls]} · ${license}`}>{short}</span>
}

export function KindIcon({ kind, size = 28 }: { kind: string; size?: number }) {
  const p = { size, strokeWidth: 1.4 }
  if (kind === 'animation') return <PersonStanding {...p} />
  if (AUDIO_KINDS.has(kind)) return <Music {...p} />
  if (kind === 'font') return <Type {...p} />
  if (kind === 'hdri') return <Sun {...p} />
  if (kind === 'material' || kind === 'texture') return <Layers {...p} />
  if (kind === 'video') return <Film {...p} />
  if (kind === 'image' || kind === 'sprite') return <ImageIcon {...p} />
  return <Box {...p} />
}

export function Badges({ kind, license, cls, rigged, animated, clips }: { kind: string; license: string; cls: LicenseClass; rigged?: boolean; animated?: boolean; clips?: number }) {
  return (
    <div className="badges">
      <LicenseBadge license={license} cls={cls} />
      <span className="badge kind">{kindLabel(kind)}</span>
      {rigged && <span className="badge rig">rigged</span>}
      {animated && kind !== 'animation' && <span className="badge rig">{clips ? `${clips} clips` : 'animated'}</span>}
    </div>
  )
}

/** Thumbnail that plays the asset's audio while hovered (sfx / music / voice). */
export function Thumb({ src, kind, audio, cover }: { src?: string | null; kind: string; audio?: string | null; cover?: boolean }) {
  const ref = useRef<HTMLAudioElement | null>(null)
  const [bad, setBad] = useState(false)
  const [playing, setPlaying] = useState(false)
  useEffect(() => () => { ref.current?.pause() }, [])
  const enter = () => {
    if (!audio) return
    if (!ref.current) { ref.current = new Audio(audio); ref.current.volume = 0.6; ref.current.onended = () => setPlaying(false) }
    ref.current.currentTime = 0
    ref.current.play().then(() => setPlaying(true)).catch(() => {})
  }
  const leave = () => { ref.current?.pause(); setPlaying(false) }
  return (
    <div className={`hub-thumb${cover ? ' cover' : ''}`} onMouseEnter={enter} onMouseLeave={leave}>
      {src && !bad ? <img src={src} loading="lazy" alt="" onError={() => setBad(true)} /> : <KindIcon kind={kind} size={34} />}
      {audio && <span className="play" title="Hover to listen"><Play size={11} fill={playing ? '#38c98a' : 'none'} /></span>}
    </div>
  )
}

let toastTimer = 0
export function toast(msg: string) {
  let el = document.getElementById('hub-toast')
  if (!el) { el = document.createElement('div'); el.id = 'hub-toast'; el.className = 'hub-toast'; document.body.appendChild(el) }
  el.textContent = msg; el.style.display = 'block'
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { if (el) el.style.display = 'none' }, 3500)
}

export interface EngineResult { ok: boolean; message: string; sent: { id: string; name: string }[]; skipped: { id: string; name?: string; reason: string }[] }

/** Per-asset actions shared by the grid, detail dialog and bulk bar. */
export function useAssetActions() {
  const useInVideo = useCallback(async (a: HubAsset) => {
    const m = await mediaFor(a)
    if (!m) return toast('This asset is not in the media library (only images, audio and video are).')
    emit('cutlab:add-to-timeline', { media: m })
  }, [])
  const openInImage = useCallback(async (a: HubAsset) => {
    const m = await mediaFor(a)
    if (!m || m.kind !== 'image') return toast('Only image assets can be opened in the image editor.')
    emit('cutlab:open-image', { media: m })
  }, [])
  const sendToEngine = useCallback(async (ids: string[]) => {
    try {
      const r = await api.post<EngineResult>('/api/hub/send/my-engine', { asset_ids: ids })
      const skipped = r.skipped.length ? ` · skipped ${r.skipped.length}: ${r.skipped.slice(0, 2).map((s) => `${s.name ?? s.id} (${s.reason})`).join('; ')}` : ''
      toast(r.message + skipped)
      return r
    } catch (e: any) { toast(e.message); return null }
  }, [])
  const copyAttribution = useCallback(async (a: HubAsset) => {
    const ok = await copyText(a.attribution || a.name)
    toast(ok ? 'Attribution copied' : 'Could not copy')
  }, [])
  return { useInVideo, openInImage, sendToEngine, copyAttribution }
}

export const audioUrl = (a: HubAsset) => (AUDIO_KINDS.has(a.kind) ? hubFile(a.id, 'main') : null)
export const isMediaKind = (a: HubAsset) => !!a.media_id
