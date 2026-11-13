import { useRef, useState } from 'react'
import { Captions, FileText, Mic, Sparkles } from 'lucide-react'
import { api } from '../../../api/client'
import { useJobSafe as useJob } from '../hooks'
import { JobBar } from '../../../shared/ui'
import { clock } from '../engine/clock'
import { useCatalog } from '../data'
import { useProject } from '../store'
import { DND_MIME, openTextFile, parseSrt } from '../actions'
import type { DndPayload } from '../actions'
import { clipDuration } from '../../../types/project'
import type { MediaRow } from '../../../types/api'
import { emit } from '../../../shared/bus'
import { useMedia } from '../data'
import { Section } from '../ui'

export default function TextPanel() {
  const presets = useCatalog((s) => s.catalog.textPresets)
  const addTextPreset = useProject((s) => s.addTextPreset)
  const [lines, setLines] = useState('')
  const [span, setSpan] = useState(10)
  const [msg, setMsg] = useState('')
  const srtRef = useRef<HTMLInputElement>(null)
  const [voice, setVoice] = useState('')
  const [vspeed, setVspeed] = useState(1)
  const [ttsMsg, setTtsMsg] = useState('')
  const tts = useJob<{ media: MediaRow }>()
  const asr = useJob<{ segments: { start: number; end: number; text: string }[] }>()

  // 为选中字幕生成配音: one TTS job per selected text clip, audio placed at the clip's start on an audio track
  const doTts = async () => {
    const st = useProject.getState()
    const texts = st.project.tracks.flatMap((t) => t.clips).filter((c) => c.type === 'text' && st.selection.includes(c.id) && c.text?.content?.trim())
      .sort((a, b) => a.start - b.start)
    if (!texts.length) { setTtsMsg('Select text/subtitle clips on the timeline first'); return }
    let ok = 0
    for (let i = 0; i < texts.length; i++) {
      const c = texts[i]
      setTtsMsg(`Voice-over ${i + 1}/${texts.length}…`)
      try {
        const body: Record<string, unknown> = { text: c.text!.content.trim(), speed: vspeed }
        if (voice.trim()) body.voice = voice.trim()
        const j = await tts.run(() => api.post('/api/tts/generate', body))
        const m = j.result?.media
        if (!m) continue
        useMedia.getState().upsert(m)
        emit('cutlab:media-changed', { media: m })
        await useProject.getState().applyOps([{ op: 'add_music', assetId: m.id, start: c.start }])
        ok++
      } catch (e: any) { setTtsMsg(`Voice-over failed: ${e?.message || e}`); return }
    }
    setTtsMsg(`Generated ${ok} voice-over clip(s)`)
  }

  const genLines = () => {
    const ls = lines.split('\n').map((l) => l.trim()).filter(Boolean)
    if (!ls.length) return
    const per = span / ls.length
    const t0 = clock.time
    void useProject.getState().applyOps([{ op: 'add_subtitles', segments: ls.map((text, i) => ({ start: t0 + i * per, end: t0 + (i + 1) * per, text })) }])
    setMsg(`Created ${ls.length} subtitle(s)`)
  }
  const importSrt = async (f: File) => {
    const segs = parseSrt(await openTextFile(f))
    if (!segs.length) { setMsg('No subtitles found in the file'); return }
    void useProject.getState().applyOps([{ op: 'add_subtitles', segments: segs }])
    setMsg(`Imported ${segs.length} subtitle(s)`)
  }
  const doAsr = async () => {
    const st = useProject.getState()
    let clip = null as null | ReturnType<typeof pick>
    function pick() {
      for (const t of st.project.tracks) for (const c of t.clips) {
        if ((c.type === 'video' || c.type === 'audio') && c.assetId && (st.selection.includes(c.id) || (!clip && !st.selection.length))) return c
      }
      return null
    }
    clip = pick()
    if (!clip) { setMsg('Select a video/audio clip first'); return }
    const c = clip
    try {
      const j = await asr.run(() => api.post('/api/ai/subtitles', { mediaId: c.assetId }))
      const dur = clipDuration(c)
      const segs = (j.result?.segments ?? [])
        .filter((s) => s.end > c.in && s.start < c.out)
        .map((s) => ({
          start: c.start + Math.max(0, s.start - c.in) / c.speed,
          end: Math.min(c.start + dur, c.start + (s.end - c.in) / c.speed),
          text: s.text,
        }))
      if (segs.length) { void st.applyOps([{ op: 'add_subtitles', segments: segs }]); setMsg(`Created ${segs.length} subtitle(s)`) } else setMsg('No speech detected')
    } catch { /* JobBar */ }
  }

  return (
    <div className="ve-panel-body" style={{ padding: 0 }}>
      <Section title="Text presets">
        <div className="ve-grid3">
          {presets.map((p) => {
            const s = p.text
            const outline = s.stroke && (s.strokeWidth ?? 0) > 0 ? `0 0 2px ${s.stroke}, 0 0 2px ${s.stroke}, 1px 1px 0 ${s.stroke}, -1px -1px 0 ${s.stroke}` : undefined
            return (
              <div key={p.id} className="ve-preset" draggable title={p.name}
                onDragStart={(e) => { const pl: DndPayload = { type: 'text', style: s as Record<string, unknown>, name: p.name }; e.dataTransfer.setData(DND_MIME, JSON.stringify(pl)) }}
                onClick={() => addTextPreset(s, p.name)}>
                <span style={{
                  color: s.color || '#fff', fontWeight: s.bold ? 700 : 400, fontStyle: s.italic ? 'italic' : 'normal',
                  textShadow: outline, background: s.bg || undefined, padding: s.bg ? '1px 6px' : undefined, fontSize: 18,
                }}>Text</span>
                <em>{p.name}</em>
              </div>
            )
          })}
        </div>
      </Section>
      <Section title="Subtitles" right={<Captions size={13} />}>
        <textarea className="textarea" rows={4} placeholder="One subtitle per line; they are spread evenly over the duration below, starting at the playhead" value={lines} onChange={(e) => setLines(e.target.value)} />
        <div className="row" style={{ margin: '6px 0' }}>
          <span className="muted">Total</span>
          <input className="input" type="number" min={1} style={{ width: 70 }} value={span} onChange={(e) => setSpan(Math.max(1, +e.target.value))} />
          <span className="muted">sec</span>
          <div className="grow" />
          <button className="btn sm primary" onClick={genLines}>Create subtitles</button>
        </div>
        <div className="row">
          <button className="btn sm" onClick={() => srtRef.current?.click()}><FileText size={13} />Import SRT</button>
          <input ref={srtRef} type="file" accept=".srt,.vtt,.txt" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void importSrt(f); e.target.value = '' }} />
          <button className="btn sm" onClick={doAsr} disabled={asr.busy} title="Transcribe the selected clip into subtitles"><Sparkles size={13} />AI subtitles</button>
        </div>
        <JobBar job={asr.job && asr.busy ? asr.job : null} error={asr.error} onCancel={asr.cancel} />
        {msg && <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{msg}</div>}
      </Section>
      <Section title="Voice-over (TTS)" right={<Mic size={13} />} defaultOpen={false}>
        <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>Select text/subtitle clips on the timeline; speech is generated for each one and placed on an audio track at its start.</div>
        <div className="row" style={{ marginBottom: 6 }}>
          <input className="input" placeholder="Voice (optional, empty = default)" value={voice} onChange={(e) => setVoice(e.target.value)} />
          <span className="muted" style={{ whiteSpace: 'nowrap' }}>Speed {vspeed.toFixed(2)}x</span>
        </div>
        <input className="ve-range" type="range" min={0.5} max={2} step={0.05} value={vspeed} onChange={(e) => setVspeed(+e.target.value)}
          style={{ ['--p' as any]: `${((vspeed - 0.5) / 1.5) * 100}%` }} />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn sm primary" onClick={doTts} disabled={tts.busy}><Mic size={13} />Generate voice-over for selected subtitles</button>
        </div>
        <JobBar job={tts.busy ? tts.job : null} error={tts.error} onCancel={tts.cancel} />
        {ttsMsg && <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{ttsMsg}</div>}
      </Section>
    </div>
  )
}
