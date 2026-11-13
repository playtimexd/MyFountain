import { create } from 'zustand'

/** Single-file AI tools that the Video Editor can open on a clip. */
export type AiToolId = 'upscale' | 'watermark' | 'removebg' | 'subtitles' | 'smartcut'

export const AI_TOOLS: { id: AiToolId; label: string; kinds: ('video' | 'image' | 'audio')[] }[] = [
  { id: 'upscale', label: 'Upscale', kinds: ['video', 'image'] },
  { id: 'watermark', label: 'Remove watermark', kinds: ['video', 'image'] },
  { id: 'removebg', label: 'Remove background', kinds: ['image', 'video'] },
  { id: 'subtitles', label: 'Smart subtitles', kinds: ['video', 'audio'] },
  { id: 'smartcut', label: 'Smart cut', kinds: ['video', 'audio'] },
]

interface AiFocus {
  /** bumped on every request so the same tool/media can be re-requested */
  nonce: number
  tool: AiToolId | null
  mediaId: string | null
  /** ask the Video Editor's AI panel to show `tool` for `mediaId` (switches the left panel to AI) */
  open: (tool: AiToolId, mediaId: string | null) => void
}

export const useAiFocus = create<AiFocus>((set) => ({
  nonce: 0, tool: null, mediaId: null,
  open: (tool, mediaId) => set((s) => ({ tool, mediaId, nonce: s.nonce + 1 })),
}))
