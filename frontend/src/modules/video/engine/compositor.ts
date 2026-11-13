// Canvas compositor: draws every visible clip at time t, bottom -> top.
import type { Adjust, Clip, Project, TextStyle } from '../../../types/project'
import { clipDuration } from '../../../types/project'
import type { MediaRow } from '../../../types/api'
import { interpKeyframes } from '../ops'
import { ensureFont } from './assets'
import { clipTraits } from '../clipTypes'

export interface RenderDeps {
  media: (id: string) => MediaRow | undefined
  video: (clipId: string) => HTMLVideoElement | null
  image: (clip: Clip) => HTMLImageElement | null
}

export interface ClipBox {
  cx: number; cy: number   // centre in canvas px (incl. transition offset: none for overlay)
  w: number; h: number     // size in canvas px incl. user scale
  rot: number              // degrees
}

// ---------------------------------------------------------------- text layout
interface TextLayout { lines: string[]; px: number; lineH: number; w: number; h: number; pad: number; font: string }
const measureCanvas = typeof document !== 'undefined' ? document.createElement('canvas') : (null as unknown as HTMLCanvasElement)
const mctx = measureCanvas?.getContext('2d') ?? null
const layoutCache = new Map<string, TextLayout>()

export function fontString(st: TextStyle, px: number) {
  return `${st.italic ? 'italic ' : ''}${st.bold ? '700' : '400'} ${px}px "${st.font}", "Microsoft YaHei", "PingFang SC", sans-serif`
}

const TOKEN = /[⺀-鿿＀-￯]|[^\s⺀-鿿＀-￯]+\s*|\s+/g

export function layoutText(st: TextStyle, W: number, H: number): TextLayout {
  const key = `${st.content}|${st.font}|${st.size}|${st.bold}|${st.italic}|${st.bgPadding}|${st.letterSpacing || 0}|${W}x${H}`
  const hit = layoutCache.get(key)
  if (hit) return hit
  ensureFont(st.font)
  const px = Math.max(4, st.size * H)
  const m = mctx!
  m.font = fontString(st, px)
  ;(m as any).letterSpacing = `${(st.letterSpacing || 0) * px}px`
  const maxW = W * 0.92
  const lines: string[] = []
  for (const para of String(st.content ?? '').split('\n')) {
    let cur = ''
    for (const tok of para.match(TOKEN) ?? ['']) {
      const t = cur + tok
      if (cur && m.measureText(t.trimEnd()).width > maxW) { lines.push(cur.trimEnd()); cur = tok.trimStart() } else cur = t
    }
    lines.push(cur.trimEnd())
  }
  let w = 0
  for (const l of lines) w = Math.max(w, m.measureText(l).width)
  const lineH = px * 1.25
  const out: TextLayout = { lines, px, lineH, w: Math.max(w, px * 0.5), h: lineH * lines.length, pad: (st.bgPadding || 0) * px, font: m.font }
  if (layoutCache.size > 200) layoutCache.clear()
  layoutCache.set(key, out)
  return out
}

// ---------------------------------------------------------------- filters
// Sharpen = SVG feConvolveMatrix filters (quantised to 10 strengths) injected once; referenced via ctx.filter url(#id).
let sharpReady = false
function ensureSharpFilters() {
  if (sharpReady || typeof document === 'undefined') return
  sharpReady = true
  const ns = 'http://www.w3.org/2000/svg'
  const svg = document.createElementNS(ns, 'svg')
  svg.setAttribute('width', '0'); svg.setAttribute('height', '0')
  svg.style.cssText = 'position:absolute;width:0;height:0;pointer-events:none'
  let html = ''
  for (let i = 1; i <= 10; i++) {
    const a = (i / 10) * 0.9
    html += `<filter id="cl-sharp-${i}" color-interpolation-filters="sRGB" x="0" y="0" width="100%" height="100%">` +
      `<feConvolveMatrix order="3" preserveAlpha="true" edgeMode="duplicate" kernelMatrix="0 ${-a} 0 ${-a} ${1 + 4 * a} ${-a} 0 ${-a} 0"/></filter>`
  }
  svg.innerHTML = html
  document.body.appendChild(svg)
}

export function adjustFilter(a: Adjust, H: number): string {
  const f: string[] = []
  if (a.brightness) f.push(`brightness(${(1 + a.brightness).toFixed(3)})`)
  if (a.contrast !== 1) f.push(`contrast(${a.contrast.toFixed(3)})`)
  if (a.saturation !== 1) f.push(`saturate(${a.saturation.toFixed(3)})`)
  if (a.hue) f.push(`hue-rotate(${a.hue.toFixed(1)}deg)`)
  if (a.blur > 0.01) f.push(`blur(${((a.blur * H) / 1080).toFixed(2)}px)`)
  if (a.sepia > 0) f.push(`sepia(${a.sepia.toFixed(3)})`)
  if (a.grayscale > 0) f.push(`grayscale(${a.grayscale.toFixed(3)})`)
  if (a.sharpen > 0.01) { ensureSharpFilters(); f.push(`url(#cl-sharp-${Math.max(1, Math.min(10, Math.round(a.sharpen * 10)))})`) }
  return f.length ? f.join(' ') : 'none'
}

const easeOut = (p: number) => 1 - Math.pow(1 - p, 3)

export class Compositor {
  private layer: HTMLCanvasElement | null = null
  private lctx: CanvasRenderingContext2D | null = null

  private getLayer(W: number, H: number) {
    if (!this.layer) { this.layer = document.createElement('canvas'); this.lctx = this.layer.getContext('2d')! }
    if (this.layer.width !== W || this.layer.height !== H) { this.layer.width = W; this.layer.height = H }
    return { canvas: this.layer, ctx: this.lctx! }
  }

  /** natural (pre-fit) size of a clip's content, or null if not yet known */
  private source(clip: Clip, W: number, H: number, deps: RenderDeps): { el: CanvasImageSource | null; sw: number; sh: number; text?: TextLayout } | null {
    if (clip.type === 'text') {
      if (!clip.text) return null
      const lay = layoutText(clip.text, W, H)
      return { el: null, sw: lay.w + lay.pad * 2, sh: lay.h + lay.pad * 2, text: lay }
    }
    if (clip.type === 'video') {
      const v = deps.video(clip.id)
      const m = clip.assetId ? deps.media(clip.assetId) : undefined
      const ready = !!v && v.readyState >= 2 && v.videoWidth > 0
      return { el: ready ? v : null, sw: ready ? v!.videoWidth : m?.width || 1920, sh: ready ? v!.videoHeight : m?.height || 1080 }
    }
    const im = deps.image(clip)
    if (im) return { el: im, sw: im.naturalWidth, sh: im.naturalHeight }
    const m = clip.assetId ? deps.media(clip.assetId) : undefined
    return { el: null, sw: m?.width || 512, sh: m?.height || 512 }
  }

  private baseSize(clip: Clip, sw: number, sh: number, W: number, H: number) {
    if (clip.type === 'text') return { w: sw, h: sh }
    const s = clip.fit === 'cover' ? Math.max(W / sw, H / sh) : Math.min(W / sw, H / sh)
    return { w: sw * s, h: sh * s }
  }

  /** Box for hit-testing / on-canvas handles. `lt` = time into clip. */
  box(clip: Clip, W: number, H: number, lt: number, deps: RenderDeps): ClipBox | null {
    const src = this.source(clip, W, H, deps)
    if (!src) return null
    const kf = interpKeyframes(clip, lt)
    const b = this.baseSize(clip, src.sw, src.sh, W, H)
    return { cx: W / 2 + kf.x * W, cy: H / 2 + kf.y * H, w: b.w * kf.scale, h: b.h * kf.scale, rot: kf.rotation }
  }

  render(ctx: CanvasRenderingContext2D, W: number, H: number, project: Project, t: number, deps: RenderDeps) {
    ctx.save()
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.globalAlpha = 1
    ctx.globalCompositeOperation = 'source-over'
    ctx.filter = 'none'
    ctx.fillStyle = project.background || '#000'
    ctx.fillRect(0, 0, W, H)
    for (const tr of project.tracks) {
      if (tr.hidden || tr.kind === 'audio') continue
      for (const clip of tr.clips) {
        if (!clipTraits(clip.type).visual) continue
        const dur = clipDuration(clip)
        const lt = t - clip.start
        if (lt < 0 || lt >= dur) continue
        this.drawClip(ctx, W, H, clip, lt, dur, deps)
      }
    }
    ctx.restore()
  }

  private drawClip(ctx: CanvasRenderingContext2D, W: number, H: number, clip: Clip, lt: number, dur: number, deps: RenderDeps) {
    const src = this.source(clip, W, H, deps)
    if (!src) return
    if (!src.el && clip.type !== 'text') return
    const kf = interpKeyframes(clip, lt)
    let alpha = kf.opacity
    if (clip.fadeIn > 0 && lt < clip.fadeIn) alpha *= lt / clip.fadeIn
    if (clip.fadeOut > 0 && lt > dur - clip.fadeOut) alpha *= Math.max(0, (dur - lt) / clip.fadeOut)
    if (alpha <= 0.001) return

    let tx = 0, ty = 0, ts = 1
    let wipe: [number, number, number, number] | null = null
    const tr = clip.transitionIn
    if (tr && tr.duration > 0 && lt < tr.duration) {
      const p = Math.min(1, lt / tr.duration), e = easeOut(p)
      switch (tr.type) {
        case 'fade': alpha *= p; break
        case 'slideleft': tx = (1 - e) * W; break
        case 'slideright': tx = -(1 - e) * W; break
        case 'slideup': ty = (1 - e) * H; break
        case 'slidedown': ty = -(1 - e) * H; break
        case 'zoomin': ts = 0.55 + 0.45 * e; alpha *= e; break
        case 'wipeleft': wipe = [0, 0, W * e, H]; break
        case 'wiperight': wipe = [W * (1 - e), 0, W * e, H]; break
      }
    }

    const { w: bw, h: bh } = this.baseSize(clip, src.sw, src.sh, W, H)
    const sc = kf.scale * ts
    const w = bw, h = bh
    const a = clip.adjust
    const filter = adjustFilter(a, H)
    const needLayer = a.temperature !== 0 || a.vignette > 0

    const target = needLayer ? this.getLayer(W, H) : null
    const c = target ? target.ctx : ctx
    if (target) { c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1; c.filter = 'none'; c.globalCompositeOperation = 'source-over'; c.clearRect(0, 0, W, H) }

    if (!target) {
      ctx.save()
      ctx.globalAlpha = alpha
      if (wipe) { ctx.beginPath(); ctx.rect(wipe[0], wipe[1], wipe[2], wipe[3]); ctx.clip() }
    } else c.save()

    c.translate(W / 2 + kf.x * W + tx, H / 2 + kf.y * H + ty)
    if (kf.rotation) c.rotate((kf.rotation * Math.PI) / 180)
    c.scale((clip.transform.flipX ? -1 : 1) * sc, (clip.transform.flipY ? -1 : 1) * sc)

    if (clip.type === 'text' && src.text && clip.text) this.drawText(c, clip.text, src.text, filter)
    else {
      if (filter !== 'none') c.filter = filter
      c.drawImage(src.el as CanvasImageSource, -w / 2, -h / 2, w, h)
      c.filter = 'none'
    }

    if (target) {
      c.globalCompositeOperation = 'source-atop'
      if (a.temperature !== 0) {
        const warm = a.temperature > 0
        c.fillStyle = warm ? `rgba(255,150,40,${Math.min(0.5, Math.abs(a.temperature) * 0.32)})` : `rgba(40,130,255,${Math.min(0.5, Math.abs(a.temperature) * 0.32)})`
        c.fillRect(-w / 2 - 2, -h / 2 - 2, w + 4, h + 4)
      }
      if (a.vignette > 0) {
        const r = Math.hypot(w, h) / 2
        const g = c.createRadialGradient(0, 0, r * (0.75 - a.vignette * 0.55), 0, 0, r)
        g.addColorStop(0, 'rgba(0,0,0,0)')
        g.addColorStop(1, `rgba(0,0,0,${Math.min(0.92, a.vignette * 0.9)})`)
        c.fillStyle = g
        c.fillRect(-w / 2 - 2, -h / 2 - 2, w + 4, h + 4)
      }
      c.restore()
      ctx.save()
      ctx.globalAlpha = alpha
      if (wipe) { ctx.beginPath(); ctx.rect(wipe[0], wipe[1], wipe[2], wipe[3]); ctx.clip() }
      ctx.drawImage(target.canvas, 0, 0)
      ctx.restore()
    } else ctx.restore()
  }

  private drawText(c: CanvasRenderingContext2D, st: TextStyle, lay: TextLayout, filter: string) {
    const { px, lineH, w, h, pad } = lay
    if (filter !== 'none') c.filter = filter
    if (st.bg) { c.fillStyle = st.bg; c.fillRect(-w / 2 - pad, -h / 2 - pad, w + pad * 2, h + pad * 2) }
    c.font = lay.font
    ;(c as any).letterSpacing = `${(st.letterSpacing || 0) * px}px`
    c.textBaseline = 'middle'
    c.textAlign = st.align || 'center'
    c.lineJoin = 'round'
    const x = st.align === 'left' ? -w / 2 : st.align === 'right' ? w / 2 : 0
    let first = true
    const hasStroke = !!st.stroke && st.strokeWidth > 0
    for (let i = 0; i < lay.lines.length; i++) {
      const y = -h / 2 + lineH * (i + 0.5)
      if (hasStroke) {
        if (st.shadow && first) { c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = px * 0.1; c.shadowOffsetX = px * 0.04; c.shadowOffsetY = px * 0.05 }
        c.lineWidth = st.strokeWidth * px * 2
        c.strokeStyle = st.stroke
        c.strokeText(lay.lines[i], x, y)
        c.shadowColor = 'transparent'
      }
      if (st.shadow && !hasStroke) { c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = px * 0.1; c.shadowOffsetX = px * 0.04; c.shadowOffsetY = px * 0.05 }
      c.fillStyle = st.color
      c.fillText(lay.lines[i], x, y)
      c.shadowColor = 'transparent'
      first = false
    }
    c.filter = 'none'
  }
}

/** Is canvas-space point (px,py) inside the rotated box? */
export function hitBox(b: ClipBox, px: number, py: number): boolean {
  const r = (-b.rot * Math.PI) / 180
  const dx = px - b.cx, dy = py - b.cy
  const x = dx * Math.cos(r) - dy * Math.sin(r)
  const y = dx * Math.sin(r) + dy * Math.cos(r)
  return Math.abs(x) <= b.w / 2 && Math.abs(y) <= b.h / 2
}
