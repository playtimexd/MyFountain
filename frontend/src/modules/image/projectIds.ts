// Which project-store id each open document belongs to (kept out of Doc so the editor core stays unaware of projects).
import type { Doc } from './doc'

export const docPid = new WeakMap<Doc, string>()
/** Doc revision last written to the project store (-1 = never). */
export const docSavedRev = new WeakMap<Doc, number>()
