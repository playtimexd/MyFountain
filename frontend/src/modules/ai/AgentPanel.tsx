import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, ChevronDown, ChevronRight, Send, Trash2, Wrench } from 'lucide-react'
import { api } from '../../api/client'
import type { Op } from '../../types/project'
import { useMediaList } from './MediaPicker'
import { allClips, fmtDur } from './aiUtil'
import type { AiPanelProps } from './aiUtil'

interface ToolStep { tool: string; args: Record<string, unknown>; result: unknown; ms: number }
interface Msg { role: 'user' | 'assistant'; content: string; ops?: Op[]; trace?: ToolStep[]; dropped?: { reason: string }[]; applied?: boolean; error?: boolean }

const SUGGEST = ['Cut out the silent parts', 'Generate relaxing background music and add it to the timeline', 'Add a title "My Trip" at the start', 'Which videos are in my library?']

export default function AgentPanel({ project, selection, applyOps, standalone }: AiPanelProps) {
  const [msgs, setMsgs] = useState<Msg[]>(() => { try { return JSON.parse(sessionStorage.getItem('cutlab.agent.msgs') || '[]') } catch { return [] } })
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  const { items: media, reload } = useMediaList()
  const names = useMemo(() => {
    const m: Record<string, string> = {}
    media.forEach((x) => { m[x.id] = x.name })
    allClips(project).forEach((c) => { m[c.id] = c.name || (c.assetId && m[c.assetId]) || (c.text?.content ? `“${c.text.content}”` : c.type) })
    return m
  }, [media, project])
  useEffect(() => { try { sessionStorage.setItem('cutlab.agent.msgs', JSON.stringify(msgs.slice(-30))) } catch { /* ignore */ } }, [msgs])
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [msgs, busy])

  const send = async (text?: string) => {
    const content = (text ?? input).trim()
    if (!content || busy) return
    setInput('')
    const next: Msg[] = [...msgs, { role: 'user', content }]
    setMsgs(next)
    setBusy(true)
    try {
      const r = await api.post<{ reply: string; ops: Op[]; toolTrace: ToolStep[]; droppedOps?: { reason: string }[] }>('/api/agent/chat', {
        messages: next.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content })), project, selection,
      })
      setMsgs([...next, { role: 'assistant', content: r.reply, ops: r.ops, trace: r.toolTrace, dropped: r.droppedOps }])
      reload()
    } catch (e: any) {
      setMsgs([...next, { role: 'assistant', content: `Something went wrong: ${e.message}`, error: true }])
    }
    setBusy(false)
  }

  const apply = (i: number) => {
    const m = msgs[i]
    if (!m.ops?.length || !applyOps) return
    applyOps(m.ops)
    setMsgs(msgs.map((x, j) => (j === i ? { ...x, applied: true } : x)))
  }

  const nm = (id?: string) => (id && names[id]) || (id ? id.slice(0, 6) : '?')
  const opLabel = (o: Op): string => {
    switch (o.op) {
      case 'add_clip': return `Add media "${nm(o.assetId)}"${o.start != null ? ` at ${fmtDur(o.start)}` : ''}${o.in != null || o.out != null ? ` (use ${fmtDur(o.in ?? 0)}–${o.out != null ? fmtDur(o.out) : 'end'})` : ''}`
      case 'split': return `Split "${nm(o.clipId)}" at ${fmtDur(o.at)}`
      case 'delete_clip': return `Delete clip "${nm(o.clipId)}"`
      case 'trim': return `Trim "${nm(o.clipId)}" in ${o.in != null ? fmtDur(o.in) : '-'} out ${o.out != null ? fmtDur(o.out) : '-'}`
      case 'move': return `Move "${nm(o.clipId)}" to ${fmtDur(o.start)}`
      case 'set': return `Change "${nm(o.clipId)}": ${Object.keys(o.patch).join(', ')}`
      case 'set_speed': return `"${nm(o.clipId)}" speed x${o.speed}`
      case 'add_transition': return `Add ${o.type} transition to "${nm(o.clipId)}" (${o.duration}s)`
      case 'add_text': return `Add text "${o.text}" at ${fmtDur(o.start)} for ${o.duration}s`
      case 'add_music': return `Add music "${nm(o.assetId)}"${o.start != null ? ` from ${fmtDur(o.start)}` : ''}${o.volume != null ? ` at volume ${o.volume}` : ''}`
      case 'add_subtitles': return `Add ${o.segments.length} subtitle line(s)`
      case 'remove_ranges': return `Delete ${o.ranges.length} range(s) (${o.ranges.reduce((s, r) => s + r.end - r.start, 0).toFixed(1)}s total): ${o.ranges.slice(0, 3).map((r) => `${fmtDur(r.start)}–${fmtDur(r.end)}`).join(', ')}${o.ranges.length > 3 ? ' ...' : ''}`
      default: return JSON.stringify(o)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div className="row" style={{ padding: '10px 12px 6px' }}>
        <Bot size={16} /><b>AI Editing Assistant</b><span className="grow" />
        {msgs.length > 0 && <button className="btn ghost sm" onClick={() => setMsgs([])} title="Clear conversation"><Trash2 size={13} /></button>}
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0 12px' }}>
        {msgs.length === 0 && (
          <div style={{ padding: '12px 0' }}>
            <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
              Describe the edit you want in plain language. The assistant looks up your media, analyses audio and proposes changes you can apply in one click.{standalone ? ' (Standalone mode: suggestions only, the timeline is not changed.)' : ''}
            </div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
              {SUGGEST.map((s) => <button key={s} className="chip" style={{ cursor: 'pointer' }} onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ marginBottom: 10, display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
            <div style={{
              maxWidth: '94%', padding: '6px 10px', borderRadius: 10, whiteSpace: 'pre-wrap', wordBreak: 'break-word', userSelect: 'text',
              background: m.role === 'user' ? 'var(--accent-soft)' : 'var(--bg-2)', border: '1px solid ' + (m.error ? 'var(--danger)' : 'var(--line)'),
              color: m.error ? 'var(--danger)' : undefined,
            }}>{m.content}</div>
            {m.ops && m.ops.length > 0 && (
              <div className="panel" style={{ marginTop: 6, width: '94%', padding: 8 }}>
                <div className="faint" style={{ fontSize: 11, marginBottom: 4 }}>Suggested changes ({m.ops.length})</div>
                <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                  {m.ops.map((o, k) => <li key={k} style={{ marginBottom: 2, userSelect: 'text' }}>{opLabel(o)}</li>)}
                </ol>
                {applyOps
                  ? <button className="btn primary sm" style={{ marginTop: 6 }} disabled={m.applied} onClick={() => apply(i)}>{m.applied ? 'Applied (Ctrl+Z to undo)' : 'Apply to timeline'}</button>
                  : <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>Open the assistant in the Video Editor to apply changes with one click.</div>}
              </div>
            )}
            {m.dropped && m.dropped.length > 0 && <div className="faint" style={{ fontSize: 11, marginTop: 2 }}>Ignored {m.dropped.length} invalid operation(s)</div>}
            {m.trace && m.trace.length > 0 && <TraceView trace={m.trace} />}
          </div>
        ))}
        {busy && <div className="faint" style={{ fontSize: 12, padding: '4px 0' }}>Thinking... (a local model can take tens of seconds)</div>}
        <div ref={end} />
      </div>
      <div style={{ padding: 10, borderTop: '1px solid var(--line)' }}>
        <div className="row" style={{ alignItems: 'flex-end' }}>
          <textarea className="textarea" rows={2} style={{ minHeight: 44 }} value={input} placeholder="e.g. cut out the silences and add some relaxing music"
            onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
          <button className="btn primary icon" disabled={busy || !input.trim()} onClick={() => send()} title="Send (Enter)"><Send size={14} /></button>
        </div>
        {selection && (selection.clipIds.length > 0) && <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>{selection.clipIds.length} clip(s) selected; sent as context.</div>}
      </div>
    </div>
  )
}

function TraceView({ trace }: { trace: ToolStep[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ width: '94%', marginTop: 4 }}>
      <button className="btn ghost sm" style={{ padding: '1px 4px' }} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}<Wrench size={12} />Used {trace.length} tool(s)
      </button>
      {open && trace.map((t, i) => (
        <details key={i} style={{ fontSize: 11, margin: '2px 0 2px 16px' }}>
          <summary style={{ cursor: 'pointer' }}>{t.tool}({Object.entries(t.args).map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 18) : JSON.stringify(v)}`).join(', ')}) <span className="faint">{t.ms}ms</span></summary>
          <pre style={{ margin: '2px 0', maxHeight: 140, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: 'var(--bg)', padding: 6, borderRadius: 4, userSelect: 'text' }}>
            {typeof t.result === 'string' ? t.result : JSON.stringify(t.result, null, 1)}
          </pre>
        </details>
      ))}
    </div>
  )
}
