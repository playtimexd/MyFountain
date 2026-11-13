import { useMemo } from 'react'
import type { PanelProps } from '../../../shared/panelTypes'
import { emit } from '../../../shared/bus'
import { useProject } from '../store'
import { useMedia } from '../data'

/** PanelProps handed to AI panels (project, selection, playhead, applyOps, onAddAsset). */
export function usePanelProps(): PanelProps {
  const project = useProject((s) => s.project)
  const selection = useProject((s) => s.selection)
  const playhead = useProject((s) => s.playhead)
  const applyOps = useProject((s) => s.applyOps)
  return useMemo<PanelProps>(() => {
    const mediaIds = selection
      .map((id) => { for (const t of project.tracks) { const c = t.clips.find((x) => x.id === id); if (c) return c.assetId } return undefined })
      .filter((x): x is string => !!x)
    return {
      project, playhead,
      selection: { clipIds: selection, mediaIds },
      applyOps: (ops) => { void applyOps(ops) },
      replaceClip: (clipId, m) => {
        useMedia.getState().upsert(m)
        emit('cutlab:media-changed', { media: m })
        useProject.getState().setClipField([clipId], { assetId: m.id, name: m.name }, undefined)
      },
      onAddAsset: (m, opts) => {
        useMedia.getState().upsert(m)
        emit('cutlab:media-changed', { media: m })
        if (opts?.toTimeline !== false) useProject.getState().addMedia(m)
      },
    }
  }, [project, selection, playhead, applyOps])
}
