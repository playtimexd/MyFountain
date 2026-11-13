// Core types for the CutLab image editor engine.

export const BLEND_MODES = [
  ['normal', 'Normal'], ['multiply', 'Multiply'], ['screen', 'Screen'], ['overlay', 'Overlay'],
  ['darken', 'Darken'], ['lighten', 'Lighten'], ['color-dodge', 'Color Dodge'], ['color-burn', 'Color Burn'],
  ['hard-light', 'Hard Light'], ['soft-light', 'Soft Light'], ['difference', 'Difference'], ['exclusion', 'Exclusion'],
  ['hue', 'Hue'], ['saturation', 'Saturation'], ['color', 'Color'], ['luminosity', 'Luminosity'],
] as const
export type BlendMode = (typeof BLEND_MODES)[number][0]
export const gco = (b: BlendMode): GlobalCompositeOperation => (b === 'normal' ? 'source-over' : b)

export interface Rect { x: number; y: number; w: number; h: number }

/** layer-local canvas -> doc space: translate(cx,cy) rotate(rot) scale(sx,sy) drawImage(canvas, -w/2, -h/2) */
export interface Transform { cx: number; cy: number; sx: number; sy: number; rot: number }

export interface AdjustItem { id: string; type: string; enabled: boolean; p: Record<string, number> }

export interface FxShadow { on: boolean; color: string; dx: number; dy: number; blur: number; opacity: number }
export interface FxStroke { on: boolean; color: string; width: number; opacity: number }
export interface FxGlow { on: boolean; color: string; size: number; opacity: number }
export interface Effects { shadow: FxShadow; stroke: FxStroke; glow: FxGlow }

export interface TextProps {
  text: string; font: string; size: number; color: string
  bold: boolean; italic: boolean; underline: boolean; align: 'left' | 'center' | 'right'
  strokeColor: string; strokeWidth: number
  shadowOn: boolean; shadowColor: string; shadowBlur: number; shadowX: number; shadowY: number
  letterSpacing: number; lineHeight: number
  bgOn: boolean; bgColor: string; bgPad: number
}

export type ShapeKind = 'rect' | 'ellipse' | 'line' | 'polygon' | 'star'
export interface ShapeProps {
  kind: ShapeKind; w: number; h: number
  fillOn: boolean; fill: string; strokeOn: boolean; stroke: string; strokeWidth: number
  radius: number; sides: number; flipY: boolean
}

export type LayerType = 'raster' | 'image' | 'text' | 'shape' | 'group'

export interface LayerMask { canvas: HTMLCanvasElement; enabled: boolean }

export interface Layer {
  id: string
  name: string
  type: LayerType
  visible: boolean
  locked: boolean
  opacity: number
  blend: BlendMode
  canvas: HTMLCanvasElement
  t: Transform
  mask: LayerMask | null
  editMask: boolean
  clip: boolean
  adjust: AdjustItem[]
  fx: Effects
  text?: TextProps
  shape?: ShapeProps
  /** tree: id of the containing group (undefined = top level) */
  parent?: string
  collapsed?: boolean
  /** group only: children blend straight into what is below (pass through) */
  pass?: boolean
  // runtime
  rev: number // any change (thumbnails / UI)
  px: number // pixel-content revision (autosave)
  cache: HTMLCanvasElement | null
  cacheCv: HTMLCanvasElement | null
  cacheOk: boolean
  savedPx?: number
  savedUrl?: string
  savedMaskUrl?: string
  savedMaskPx?: number
}

export interface Sel {
  w: number; h: number
  mask: Uint8ClampedArray
  canvas: HTMLCanvasElement
  bbox: Rect
  ants: Path2D | null
  antsCount: number
}

export type ToolId =
  | 'move' | 'marquee' | 'lasso' | 'wand' | 'crop' | 'brush' | 'eraser' | 'bucket' | 'gradient' | 'eyedropper'
  | 'clone' | 'heal' | 'retouch' | 'tone' | 'text' | 'shape' | 'hand' | 'zoom' | 'aibrush' | 'quicksel'

export type SelMode = 'replace' | 'add' | 'subtract' | 'intersect'
