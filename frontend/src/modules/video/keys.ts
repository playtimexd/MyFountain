import type { Clip, Project } from '../../types/project'

export const allClipsInOrder = (p: Project): Clip[] => p.tracks.flatMap((t) => t.clips)
