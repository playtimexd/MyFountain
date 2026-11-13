import type { MediaRow } from '../types/api'
import type { Op, Project } from '../types/project'

/** Props every AI side-panel receives from whichever editor hosts it. */
export interface PanelProps {
  /** Add a (freshly generated / found) media row to the host editor (library + timeline/canvas as appropriate). */
  onAddAsset: (media: MediaRow, opts?: { toTimeline?: boolean }) => void
  /** Current video project (undefined inside the image editor). */
  project?: Project
  /** Currently selected clip ids / media ids in the host editor. */
  selection?: { clipIds: string[]; mediaIds: string[] }
  /** Apply agent / auto-cut operations to the host editor as ONE undoable step. */
  applyOps?: (ops: Op[]) => void
  /** Current playhead (seconds) in the video editor. */
  playhead?: number
  /** Swap the media behind a timeline clip (one undoable step). Only the video editor provides this. */
  replaceClip?: (clipId: string, media: MediaRow) => void
}
