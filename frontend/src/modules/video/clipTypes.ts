// Central per-clip-type traits. Adding a new clip type (e.g. 'animation') = add one entry here, then handle its
// pixel source in engine/compositor.ts `source()` and its image/video element in engine/player.ts deps.
import type { Clip, ClipType, TrackKind } from '../../types/project'

export interface ClipTraits {
  track: TrackKind        // which track kind hosts it
  visual: boolean         // drawn on the preview canvas
  audible: boolean        // has an audio stream played by the media pool
  timedSource: boolean    // has a seekable source range (in/out are source seconds)
  videoElement: boolean   // needs a pooled <video> element
  adjustable: boolean     // colour adjust / filter apply
  transformFit: boolean   // content is fit (contain/cover) into the canvas before scale (false for text)
}

const T = (o: Partial<ClipTraits> & { track: TrackKind }): ClipTraits => ({
  visual: true, audible: false, timedSource: false, videoElement: false, adjustable: true, transformFit: true, ...o,
})

const TRAITS: Record<string, ClipTraits> = {
  video: T({ track: 'video', audible: true, timedSource: true, videoElement: true }),
  image: T({ track: 'video' }),
  sticker: T({ track: 'video' }),
  audio: T({ track: 'audio', visual: false, audible: true, timedSource: true, videoElement: true, adjustable: false }),
  text: T({ track: 'text', adjustable: false, transformFit: false }),
}

export const clipTraits = (t: ClipType | string): ClipTraits => TRAITS[t] ?? TRAITS.image
export const traitsOf = (c: Clip) => clipTraits(c.type)
