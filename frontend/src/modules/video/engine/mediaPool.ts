// Pool of HTMLVideoElements kept in sync with the master clock. Elements carry the audio too.
import type { Clip, Project } from '../../../types/project'
import { clipDuration } from '../../../types/project'
import { mediaUrl } from '../../../api/client'
import { clipTraits } from '../clipTypes'

const LOOKAHEAD = 1.6
const LINGER = 0.4
const DRIFT = 0.15

// WebAudio routing is only used for elements that ever need volume > 1 (createMediaElementSource is irreversible,
// so a routed element keeps using its GainNode for every later clip it plays).
let actx: AudioContext | null = null
export const routes = new WeakMap<HTMLVideoElement, GainNode>()
export function resumeAudio() { try { void actx?.resume() } catch { /* noop */ } }
function routeElement(el: HTMLVideoElement): GainNode | null {
  try {
    if (!actx) actx = new AudioContext()
    let g = routes.get(el)
    if (!g) {
      const src = actx.createMediaElementSource(el)
      g = actx.createGain()
      src.connect(g); g.connect(actx.destination)
      routes.set(el, g)
    }
    if (actx.state === 'suspended') void actx.resume().catch(() => {})
    return g
  } catch { return null }
}

interface Entry { el: HTMLVideoElement; clipId: string; url: string }

export class MediaPool {
  private active = new Map<string, Entry>()
  private free: HTMLVideoElement[] = []
  private onVisual: () => void
  constructor(onVisual: () => void) { this.onVisual = onVisual }

  video(clipId: string): HTMLVideoElement | null { return this.active.get(clipId)?.el ?? null }

  private acquire(url: string, clipId: string): Entry {
    let el: HTMLVideoElement | undefined
    const i = this.free.findIndex((e) => e.dataset.url === url)
    if (i >= 0) el = this.free.splice(i, 1)[0]
    else el = this.free.pop()
    if (!el) {
      el = document.createElement('video')
      el.preload = 'auto'
      el.playsInline = true
      el.crossOrigin = 'anonymous'
      el.addEventListener('seeked', this.onVisual)
      el.addEventListener('loadeddata', this.onVisual)
      el.addEventListener('canplay', this.onVisual)
    }
    if (el.dataset.url !== url) { el.src = url; el.dataset.url = url; el.load() }
    const e = { el, clipId, url }
    this.active.set(clipId, e)
    return e
  }

  private release(clipId: string) {
    const e = this.active.get(clipId)
    if (!e) return
    try { e.el.pause() } catch { /* noop */ }
    this.active.delete(clipId)
    this.free.push(e.el)
    // keep at most a few idle elements; drop the oldest source to free decoders
    while (this.free.length > 4) {
      const old = this.free.shift()!
      old.removeAttribute('src'); delete old.dataset.url; old.load()
    }
  }

  pauseAll() { for (const e of this.active.values()) { try { e.el.pause() } catch { /* noop */ } } }

  dispose() {
    for (const id of [...this.active.keys()]) this.release(id)
    for (const el of this.free) { el.removeAttribute('src'); el.load() }
    this.free = []
  }

  /** Reconcile elements with the project at time t. */
  sync(project: Project, t: number, playing: boolean) {
    const needed = new Set<string>()
    for (const tr of project.tracks) {
      if (tr.kind === 'text') continue
      for (const clip of tr.clips) {
        if (!clipTraits(clip.type).videoElement || !clip.assetId) continue
        const dur = clipDuration(clip)
        if (t < clip.start - LOOKAHEAD || t > clip.start + dur + LINGER) continue
        needed.add(clip.id)
        this.syncOne(clip, tr.muted, t, dur, playing)
      }
    }
    for (const id of [...this.active.keys()]) if (!needed.has(id)) this.release(id)
  }

  private syncOne(clip: Clip, muted: boolean, t: number, dur: number, playing: boolean) {
    const url = mediaUrl(clip.assetId!)
    let e = this.active.get(clip.id)
    if (e && e.url !== url) { this.release(clip.id); e = undefined }
    if (!e) e = this.acquire(url, clip.id)
    const el = e.el
    const lt = t - clip.start
    const isActive = lt >= 0 && lt < dur
    const rate = Math.min(16, Math.max(0.0625, clip.speed || 1))
    if (el.playbackRate !== rate) el.playbackRate = rate
    // volume incl. fades (may exceed 1 -> WebAudio gain)
    let vol = clip.volume
    if (clip.fadeIn > 0 && lt < clip.fadeIn) vol *= Math.max(0, lt / clip.fadeIn)
    if (clip.fadeOut > 0 && lt > dur - clip.fadeOut) vol *= Math.max(0, (dur - lt) / clip.fadeOut)
    vol = Math.min(2, Math.max(0, vol))
    const mute = muted || !!clip.audioDetached || vol <= 0.001 || !isActive
    let gain = routes.get(el) ?? null
    if (!gain && vol > 1.001 && !mute) gain = routeElement(el)
    if (gain) {
      if (el.volume !== 1) el.volume = 1
      if (el.muted) el.muted = false
      const gv = mute ? 0 : vol
      if (Math.abs(gain.gain.value - gv) > 0.005) gain.gain.value = gv
    } else {
      if (el.muted !== mute) el.muted = mute
      const v = Math.min(1, vol)
      if (!mute && Math.abs(el.volume - v) > 0.005) el.volume = v
    }

    const target = Math.min(clip.out - 0.02, Math.max(clip.in, clip.in + (isActive ? lt : 0) * clip.speed))
    if (!isActive) {
      if (!el.paused) el.pause()
      // preload at in-point (or hold the last frame once finished)
      const park = lt < 0 ? clip.in : Math.max(clip.in, clip.out - 0.04)
      if (!el.seeking && el.readyState > 0 && Math.abs(el.currentTime - park) > 0.08) el.currentTime = park
      return
    }
    if (playing) {
      const drift = Math.abs(el.currentTime - target)
      if (el.paused) {
        if (drift > 0.05 && !el.seeking) el.currentTime = target
        const pr = el.play()
        if (pr) pr.catch(() => { /* autoplay policy / interrupted */ })
      } else if (drift > DRIFT && !el.seeking) el.currentTime = target
    } else {
      if (!el.paused) el.pause()
      if (el.readyState > 0 && Math.abs(el.currentTime - target) > 0.02 && (!el.seeking || Math.abs((el as any)._tgt - target) > 0.04)) {
        ;(el as any)._tgt = target
        el.currentTime = target
      }
    }
  }
}
