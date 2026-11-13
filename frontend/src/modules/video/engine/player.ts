// Master clock + render loop. Singleton: there is exactly one preview canvas.
import { projectDuration } from '../../../types/project'
import type { Project } from '../../../types/project'
import { Compositor } from './compositor'
import type { RenderDeps } from './compositor'
import { MediaPool, resumeAudio } from './mediaPool'
import { clock } from './clock'
import { getImage, setAssetReadyHandler } from './assets'
import { getMedia, stickerUrl } from '../data'
import { useProject } from '../store'
import { mediaUrl } from '../../../api/client'

class Player {
  canvas: HTMLCanvasElement | null = null
  private ctx: CanvasRenderingContext2D | null = null
  private comp = new Compositor()
  readonly pool = new MediaPool(() => this.requestDraw())
  private project: Project = useProject.getState().project
  private raf = 0
  private playing = false
  private wallStart = 0
  private timeStart = 0
  private lastMirror = 0

  readonly deps: RenderDeps = {
    media: getMedia,
    video: (id) => this.pool.video(id),
    image: (clip) => {
      if (!clip.assetId) return null
      return getImage(clip.type === 'sticker' ? stickerUrl(clip.assetId) : mediaUrl(clip.assetId))
    },
  }

  constructor() {
    setAssetReadyHandler(() => this.requestDraw())
  }

  get duration() { return projectDuration(this.project) }
  get isPlaying() { return this.playing }
  get compositor() { return this.comp }

  attach(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d', { alpha: false })
    this.requestDraw()
  }
  detach(c: HTMLCanvasElement) { if (this.canvas === c) { this.canvas = null; this.ctx = null } }

  resize(w: number, h: number) {
    if (!this.canvas) return
    w = Math.max(2, Math.round(w)); h = Math.max(2, Math.round(h))
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h }
    this.requestDraw()
  }

  setProject(p: Project) {
    this.project = p
    if (this.playing && clock.time > projectDuration(p)) this.pause()
    this.requestDraw()
  }

  seek(t: number, mirror = true) {
    t = Math.max(0, Math.min(t, Math.max(this.duration, 0) + 3600))
    clock.set(t)
    if (this.playing) { this.wallStart = performance.now(); this.timeStart = t }
    if (mirror) useProject.setState({ playhead: t })
    this.requestDraw()
  }

  play() {
    if (this.playing) return
    const dur = this.duration
    if (dur <= 0) return
    if (clock.time >= dur - 0.02) this.seek(0)
    resumeAudio()
    this.playing = true
    clock.playing = true
    this.wallStart = performance.now()
    this.timeStart = clock.time
    useProject.setState({ playing: true })
    this.requestDraw()
  }

  pause() {
    if (!this.playing) { this.pool.pauseAll(); return }
    this.playing = false
    clock.playing = false
    this.pool.pauseAll()
    useProject.setState({ playing: false, playhead: clock.time })
    this.requestDraw()
  }

  toggle() { this.playing ? this.pause() : this.play() }

  requestDraw = () => { if (!this.raf) this.raf = requestAnimationFrame(this.frame) }

  private frame = (now: number) => {
    this.raf = 0
    if (this.playing) {
      let t = this.timeStart + (now - this.wallStart) / 1000
      const dur = this.duration
      if (t >= dur) { t = dur; clock.set(t); this.pause(); this.draw(t); return }
      clock.set(t)
      if (now - this.lastMirror > 250) { this.lastMirror = now; useProject.setState({ playhead: t }) }
    }
    this.draw(clock.time)
    if (this.playing) this.requestDraw()
  }

  private draw(t: number) {
    const cv = this.canvas, ctx = this.ctx
    if (!cv || !ctx) return
    this.pool.sync(this.project, t, this.playing)
    this.comp.render(ctx, cv.width, cv.height, this.project, t, this.deps)
  }
}

export const player = new Player()
useProject.subscribe((s, prev) => { if (s.project !== prev.project) player.setProject(s.project) })
