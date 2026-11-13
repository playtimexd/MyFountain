import { create } from 'zustand'
import { Doc, hooks } from './doc'
import type { ShapeKind, ToolId, SelMode } from './types'

export interface Opts {
  brushSize: number; hardness: number; opacity: number; flow: number; smooth: number
  tolerance: number; contiguous: boolean; sampleAll: boolean
  marquee: 'rect' | 'ellipse'; marqueeStyle: 'normal' | 'ratio' | 'size'; mqW: number; mqH: number
  selMode: SelMode; feather: number; lasso: 'free' | 'poly'
  gradType: 'linear' | 'radial'; gradMode: 'fgbg' | 'fgtrans'; bucketAll: boolean
  shape: ShapeKind; shapeFill: boolean; shapeStroke: boolean; shapeStrokeW: number; shapeRadius: number; shapeSides: number
  retouch: 'blur' | 'sharpen' | 'smudge'; strength: number; tone: 'dodge' | 'burn'; exposure: number
  autoSelect: boolean; cropRatio: string; cropW: number; cropH: number; cloneAligned: boolean
}
export const defaultOpts: Opts = {
  brushSize: 30, hardness: 80, opacity: 100, flow: 100, smooth: 20,
  tolerance: 32, contiguous: true, sampleAll: false,
  marquee: 'rect', marqueeStyle: 'normal', mqW: 1, mqH: 1, selMode: 'replace', feather: 0, lasso: 'free',
  gradType: 'linear', gradMode: 'fgbg', bucketAll: false,
  shape: 'rect', shapeFill: true, shapeStroke: false, shapeStrokeW: 4, shapeRadius: 0, shapeSides: 6,
  retouch: 'blur', strength: 50, tone: 'dodge', exposure: 30,
  autoSelect: true, cropRatio: 'free', cropW: 16, cropH: 9, cloneAligned: true,
}

export type DialogState =
  | { kind: 'new' } | { kind: 'export' } | { kind: 'canvasSize' } | { kind: 'imageSize' }
  | { kind: 'filter'; id: string } | { kind: 'prompt'; title: string; label: string; value: number; min: number; max: number; onOk: (v: number) => void }
  | { kind: 'fill' } | { kind: 'help' } | { kind: 'closeConfirm'; docId: string }

interface UI {
  tool: ToolId
  prevTool: ToolId | null
  fg: string
  bg: string
  recent: string[]
  opts: Opts
  docs: Doc[]
  cur: string
  rev: number
  dialog: DialogState | null
  toast: { id: number; msg: string; kind: 'info' | 'error' | 'ok' } | null
  panelsOpen: Record<string, boolean>
  dockTab: string
  busy: string | null
  editingText: string | null
  rulers: boolean
  showGuides: boolean
  snap: boolean
  colorRange: import('./ColorRange').ColorRangeState | null
  setTool: (t: ToolId) => void
  setOpts: (p: Partial<Opts>) => void
  setFg: (c: string) => void
  setBg: (c: string) => void
  pushRecent: (c: string) => void
}

export const useUI = create<UI>((set, get) => ({
  tool: 'move', prevTool: null,
  fg: '#000000', bg: '#ffffff', recent: [],
  opts: defaultOpts,
  docs: [], cur: '', rev: 0, dialog: null, toast: null,
  panelsOpen: { color: true, layers: true, props: true, history: false, ai: false, assets: false },
  dockTab: 'layers', busy: null, editingText: null, rulers: true, showGuides: true, snap: true, colorRange: null,
  setTool: (tool) => set({ tool }),
  setOpts: (p) => set({ opts: { ...get().opts, ...p } }),
  setFg: (fg) => set({ fg }),
  setBg: (bg) => set({ bg }),
  pushRecent: (c) => set({ recent: [c, ...get().recent.filter((x) => x !== c)].slice(0, 16) }),
}))

export const curDoc = (): Doc | undefined => {
  const s = useUI.getState()
  return s.docs.find((d) => d.id === s.cur)
}
export function addDoc(d: Doc) {
  useUI.setState((s) => ({ docs: [...s.docs, d], cur: d.id, rev: s.rev + 1 }))
  hooks.render()
}
export function closeDoc(id: string) {
  useUI.setState((s) => {
    const docs = s.docs.filter((d) => d.id !== id)
    const cur = s.cur === id ? docs[docs.length - 1]?.id ?? '' : s.cur
    return { docs, cur, rev: s.rev + 1 }
  })
  hooks.render()
}
export function setCur(id: string) { useUI.setState((s) => ({ cur: id, rev: s.rev + 1 })); hooks.render() }
export function openDialog(d: DialogState | null) { useUI.setState({ dialog: d }) }

let toastId = 0
let toastTimer: number | undefined
hooks.toast = (msg, kind = 'info') => {
  const id = ++toastId
  useUI.setState({ toast: { id, msg, kind } })
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { if (useUI.getState().toast?.id === id) useUI.setState({ toast: null }) }, kind === 'error' ? 4500 : 2500)
}
let uiPending = false
hooks.ui = () => {
  if (uiPending) return
  uiPending = true
  requestAnimationFrame(() => { uiPending = false; useUI.setState((s) => ({ rev: s.rev + 1 })) })
}

// cursor / selection status (separate tiny store so the status bar re-renders alone)
export const useStatus = create<{ x: number; y: number; inDoc: boolean }>(() => ({ x: 0, y: 0, inDoc: false }))

/** set when a text layer was just created/selected so the properties textarea grabs focus once it mounts */
export const focusReq = { text: false }
