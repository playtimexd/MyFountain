// Tiny cross-module event bus (window CustomEvents) so Library / Downloader / AI hub can hand work to the editors
// without importing them. emit() first asks App to switch tab, then re-sends the real event once the (lazy) module is mounted.
import type { MediaRow, Segment } from '../types/api'

export interface BusEvents {
  /** Put a media (optionally a sub-range) on the video timeline. */
  'cutlab:add-to-timeline': { media: MediaRow; in?: number; out?: number }
  /** Put several ranges of one media on the timeline back-to-back. */
  'cutlab:add-segments': { media: MediaRow; segments: Segment[] }
  /** Open an image media as a new layer in the image editor. */
  'cutlab:open-image': { media: MediaRow }
  'cutlab:goto': { tab: 'video' | 'image' | 'library' | 'download' | 'models' | 'anim' | 'ai' }
  /** Open a character (rig library id) in the Animation workspace (3D Models → "Send to Animation"). */
  'cutlab:to-anim-rig': { rigId: string }
  /** Open the Fur tool in 3D Models with a source preselected: "rig:<id>", "export:<id>" or "groom:<id>". */
  'cutlab:to-fur': { source: string }
  /** Open an animation clip in the Animation workspace (from the main Library). */
  'cutlab:to-anim-clip': { clipId: string; action: 'open' | 'retarget' }
  /** Send a library video to the animation module as a motion-capture source. */
  'cutlab:to-mocap': { media: MediaRow; start?: number; end?: number }
  /** Library contents changed (import, download, generation…). */
  'cutlab:media-changed': { media?: MediaRow }
}

const TARGET: Partial<Record<keyof BusEvents, 'video' | 'image' | 'anim' | 'models'>> = {
  'cutlab:add-to-timeline': 'video', 'cutlab:add-segments': 'video', 'cutlab:open-image': 'image', 'cutlab:to-mocap': 'anim',
  'cutlab:to-anim-rig': 'anim', 'cutlab:to-fur': 'models', 'cutlab:to-anim-clip': 'anim',
}

export function emit<K extends keyof BusEvents>(name: K, detail: BusEvents[K]) {
  const tab = TARGET[name]
  if (tab) {
    window.dispatchEvent(new CustomEvent('cutlab:goto', { detail: { tab } }))
    setTimeout(() => window.dispatchEvent(new CustomEvent(name, { detail })), 500)
  } else {
    window.dispatchEvent(new CustomEvent(name, { detail }))
  }
}

export function on<K extends keyof BusEvents>(name: K, fn: (d: BusEvents[K]) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent).detail)
  window.addEventListener(name, h)
  return () => window.removeEventListener(name, h)
}
