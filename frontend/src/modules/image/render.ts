// Vector layer rasterisation (text + shapes) and font loading.
import { api } from '../../api/client'
import type { ShapeProps, TextProps } from './types'
import { clamp, ctx2, mkCanvas } from './util'

export const defaultText = (color = '#ffffff'): TextProps => ({
  text: 'Double-click to edit', font: 'Microsoft YaHei', size: 64, color, bold: false, italic: false, underline: false, align: 'left',
  strokeColor: '#000000', strokeWidth: 0, shadowOn: false, shadowColor: '#000000', shadowBlur: 8, shadowX: 3, shadowY: 3,
  letterSpacing: 0, lineHeight: 1.25, bgOn: false, bgColor: '#000000', bgPad: 12,
})
export const defaultShape = (kind: ShapeProps['kind'], w: number, h: number, fill: string, stroke: string): ShapeProps => ({
  kind, w, h, fillOn: kind !== 'line', fill, strokeOn: kind === 'line', stroke, strokeWidth: kind === 'line' ? 6 : 0,
  radius: 0, sides: kind === 'star' ? 5 : 6, flipY: false,
})

// ---------- fonts ----------
export interface FontEntry { family: string; url: string | null }
export const BUILTIN_FONTS = ['Microsoft YaHei', 'SimHei', 'SimSun', 'KaiTi', 'FangSong', 'Arial', 'Georgia', 'Times New Roman', 'Courier New', 'Impact', 'Segoe UI', 'Verdana', 'Comic Sans MS']
let fontList: FontEntry[] | null = null
let fontPromise: Promise<FontEntry[]> | null = null
const loaded = new Map<string, Promise<void>>()
const ready = new Set<string>()
export function listFonts(): Promise<FontEntry[]> {
  if (fontList) return Promise.resolve(fontList)
  fontPromise ??= api.get<FontEntry[]>('/api/assets/fonts').then((l) => (fontList = Array.isArray(l) ? l : [])).catch(() => (fontList = []))
  return fontPromise
}
/** Ensure a font family is usable on canvas (loads via FontFace when the backend gave a url). */
export function ensureFont(family: string): Promise<void> {
  const hit = loaded.get(family)
  if (hit) return hit
  const p = (async () => {
    const list = await listFonts()
    const f = list.find((x) => x.family === family)
    if (f?.url) {
      try {
        const ff = new FontFace(family, `url(${f.url})`)
        await ff.load()
        document.fonts.add(ff)
      } catch { /* fall back to system */ }
    } else {
      try { await document.fonts.load(`16px "${family}"`) } catch { /* ignore */ }
    }
  })().then(() => { ready.add(family) })
  loaded.set(family, p)
  return p
}
export const isFontReady = (family: string) => ready.has(family)

// ---------- text ----------
/** layout numbers shared by the renderer and the on-canvas editor overlay */
export function textMetrics(p: TextProps) {
  const m = ctx2(mkCanvas(8, 8))
  m.font = `${p.italic ? 'italic ' : ''}${p.bold ? 'bold ' : ''}${p.size}px "${p.font}", "Microsoft YaHei", sans-serif`
  try { m.letterSpacing = `${p.letterSpacing}px` } catch { /* old browsers */ }
  const lines = (p.text || ' ').split('\n')
  const widths = lines.map((l) => m.measureText(l).width)
  const lh = p.size * p.lineHeight
  const tw = Math.max(...widths, 1), th = lines.length * lh
  const pad = Math.ceil(Math.max(p.strokeWidth, p.shadowOn ? p.shadowBlur * 1.5 + Math.max(Math.abs(p.shadowX), Math.abs(p.shadowY)) : 0, 2) + (p.bgOn ? p.bgPad : 0) + p.size * 0.1)
  return { pad, tw, th, lh }
}
export function renderText(p: TextProps): HTMLCanvasElement {
  const m = ctx2(mkCanvas(8, 8))
  const font = `${p.italic ? 'italic ' : ''}${p.bold ? 'bold ' : ''}${p.size}px "${p.font}", "Microsoft YaHei", sans-serif`
  m.font = font
  try { m.letterSpacing = `${p.letterSpacing}px` } catch { /* old browsers */ }
  const lines = (p.text || ' ').split('\n')
  const widths = lines.map((l) => m.measureText(l).width)
  const lh = p.size * p.lineHeight
  const tw = Math.max(...widths, 1), th = lines.length * lh
  const pad = Math.ceil(Math.max(p.strokeWidth, p.shadowOn ? p.shadowBlur * 1.5 + Math.max(Math.abs(p.shadowX), Math.abs(p.shadowY)) : 0, 2) + (p.bgOn ? p.bgPad : 0) + p.size * 0.1)
  const W = clamp(Math.ceil(tw + pad * 2), 1, 8192), H = clamp(Math.ceil(th + pad * 2), 1, 8192)
  const cv = mkCanvas(W, H), c = ctx2(cv)
  if (p.bgOn) {
    c.fillStyle = p.bgColor
    const bp = p.bgPad, x = pad - bp, y = pad - bp, w = tw + bp * 2, h = th + bp * 2, r = Math.min(10, h / 4)
    c.beginPath(); c.roundRect(x, y, w, h, r); c.fill()
  }
  c.font = font
  try { c.letterSpacing = `${p.letterSpacing}px` } catch { /* ignore */ }
  c.textBaseline = 'middle'
  c.textAlign = 'left'
  c.lineJoin = 'round'
  lines.forEach((ln, i) => {
    const w = widths[i]
    const x = p.align === 'left' ? pad : p.align === 'center' ? pad + (tw - w) / 2 : pad + tw - w
    const y = pad + i * lh + lh / 2
    c.save()
    if (p.shadowOn) { c.shadowColor = p.shadowColor; c.shadowBlur = p.shadowBlur; c.shadowOffsetX = p.shadowX; c.shadowOffsetY = p.shadowY }
    if (p.strokeWidth > 0) { c.lineWidth = p.strokeWidth * 2; c.strokeStyle = p.strokeColor; c.strokeText(ln, x, y) }
    c.shadowColor = 'transparent'
    c.fillStyle = p.color
    if (p.shadowOn && p.strokeWidth <= 0) { c.shadowColor = p.shadowColor; c.shadowBlur = p.shadowBlur; c.shadowOffsetX = p.shadowX; c.shadowOffsetY = p.shadowY }
    c.fillText(ln, x, y)
    c.restore()
    if (p.underline) { c.fillStyle = p.color; c.fillRect(x, y + p.size * 0.42, w, Math.max(1, p.size / 16)) }
  })
  return cv
}

// ---------- shapes ----------
export function renderShape(s: ShapeProps): HTMLCanvasElement {
  const sw = s.strokeOn ? s.strokeWidth : 0
  const pad = Math.ceil(sw / 2) + 2
  const w = Math.max(1, Math.round(Math.abs(s.w))), h = Math.max(1, Math.round(Math.abs(s.h)))
  const cv = mkCanvas(clamp(w + pad * 2, 1, 8192), clamp(h + pad * 2, 1, 8192)), c = ctx2(cv)
  c.translate(pad, pad)
  c.beginPath()
  switch (s.kind) {
    case 'rect': c.roundRect(0, 0, w, h, Math.min(s.radius, w / 2, h / 2)); break
    case 'ellipse': c.ellipse(w / 2, h / 2, w / 2, h / 2, 0, 0, Math.PI * 2); break
    case 'line': if (s.flipY) { c.moveTo(0, h); c.lineTo(w, 0) } else { c.moveTo(0, 0); c.lineTo(w, h) } break
    case 'polygon': case 'star': {
      const n = Math.max(3, Math.round(s.sides)), star = s.kind === 'star'
      const pts = star ? n * 2 : n
      for (let i = 0; i < pts; i++) {
        const a = (i / pts) * Math.PI * 2 - Math.PI / 2
        const k = star && i % 2 ? 0.42 : 1
        const x = w / 2 + Math.cos(a) * (w / 2) * k, y = h / 2 + Math.sin(a) * (h / 2) * k
        if (i) c.lineTo(x, y); else c.moveTo(x, y)
      }
      c.closePath(); break
    }
  }
  if (s.fillOn && s.kind !== 'line') { c.fillStyle = s.fill; c.fill() }
  if (sw > 0) { c.lineWidth = sw; c.strokeStyle = s.stroke; c.lineJoin = 'round'; c.lineCap = 'round'; c.stroke() }
  return cv
}
