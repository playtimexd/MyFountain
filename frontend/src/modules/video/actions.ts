// Cross-component helpers: uploading files, drag payloads, project file IO.
import { api } from '../../api/client'
import type { MediaRow } from '../../types/api'
import type { Project } from '../../types/project'
import { useMedia } from './data'
import { normalizeProject } from './ops'
import { useProject } from './store'

export const DND_MIME = 'application/x-cutlab-asset'
export interface DndPayload {
  type: 'media' | 'sticker' | 'text'
  id?: string
  name?: string
  style?: Record<string, unknown>
}

export function hasFiles(dt: DataTransfer | null) {
  return !!dt && Array.from(dt.types || []).includes('Files')
}

export async function uploadFiles(files: File[], onProgress?: (msg: string) => void): Promise<MediaRow[]> {
  const out: MediaRow[] = []
  for (let i = 0; i < files.length; i++) {
    onProgress?.(`Uploading ${i + 1}/${files.length}: ${files[i].name}`)
    try {
      const row = await api.upload<MediaRow>('/api/media/upload', files[i])
      useMedia.getState().upsert(row)
      out.push(row)
    } catch (e: any) {
      useProject.getState().say(`Upload failed: ${files[i].name} (${e.message})`)
    }
  }
  return out
}

export function downloadProject(p: Project) {
  const blob = new Blob([JSON.stringify(p, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${p.name || 'project'}.cutlab.json`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}

export async function openProjectFile(file: File): Promise<Project | null> {
  try {
    const raw = JSON.parse(await file.text())
    const p = normalizeProject(raw.project && raw.project.tracks ? raw.project : raw)
    return p
  } catch { return null }
}

export function parseSrt(text: string): { start: number; end: number; text: string }[] {
  const out: { start: number; end: number; text: string }[] = []
  const toS = (s: string) => { const m = s.trim().replace(',', '.').split(':').map(parseFloat); return m.length === 3 ? m[0] * 3600 + m[1] * 60 + m[2] : m.length === 2 ? m[0] * 60 + m[1] : m[0] }
  for (const block of text.replace(/\r/g, '').split(/\n{2,}/)) {
    const lines = block.split('\n').filter((l) => l.trim() !== '')
    const ti = lines.findIndex((l) => l.includes('-->'))
    if (ti < 0) continue
    const [a, b] = lines[ti].split('-->')
    const body = lines.slice(ti + 1).join('\n').replace(/<[^>]+>/g, '').trim()
    if (body) out.push({ start: toS(a), end: toS(b), text: body })
  }
  return out
}

export const openTextFile = (f: File) => f.text()
