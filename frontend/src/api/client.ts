import { useCallback, useEffect, useRef, useState } from 'react'
import type { JobInfo } from '../types/api'

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try { const j = await r.json(); msg = j.detail ? (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) : msg } catch { /* noop */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  get: <T = any>(url: string) => req<T>('GET', url),
  post: <T = any>(url: string, body?: unknown) => req<T>('POST', url, body ?? {}),
  put: <T = any>(url: string, body?: unknown) => req<T>('PUT', url, body ?? {}),
  patch: <T = any>(url: string, body?: unknown) => req<T>('PATCH', url, body ?? {}),
  del: <T = any>(url: string) => req<T>('DELETE', url),
  upload: <T = any>(url: string, file: File) => { const f = new FormData(); f.append('file', file); return req<T>('POST', url, f) },
}

export const mediaUrl = (id: string) => `/api/media/${id}/file`
export const thumbUrl = (id: string) => `/api/media/${id}/thumb`

/** Resolve when a job finishes (or throw on error/cancel). `onUpdate` receives every poll. */
export async function waitJob<R = any>(job: JobInfo<R>, onUpdate?: (j: JobInfo<R>) => void): Promise<JobInfo<R>> {
  let j = job
  onUpdate?.(j)
  while (j.status === 'queued' || j.status === 'running') {
    await new Promise((r) => setTimeout(r, 600))
    j = await api.get<JobInfo<R>>(`/api/jobs/${j.id}`)
    onUpdate?.(j)
  }
  if (j.status === 'error') throw new Error(j.error || 'job failed')
  if (j.status === 'cancelled') throw new Error('Cancelled')
  return j
}

/** Start a job-returning request and track it. `run(factory)` resolves with the final job. */
export function useJob<R = any>() {
  const [job, setJob] = useState<JobInfo<R> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)
  // StrictMode mounts → unmounts → remounts in dev: the flag must be re-armed on every mount.
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const run = useCallback(async (start: () => Promise<JobInfo<R>>) => {
    setError(null)
    try {
      const j0 = await start()
      const done = await waitJob<R>(j0, (j) => alive.current && setJob(j))
      return done
    } catch (e: any) {
      if (alive.current) setError(e.message || String(e))
      throw e
    }
  }, [])
  const cancel = useCallback(() => { if (job) api.post(`/api/jobs/${job.id}/cancel`) }, [job])
  const busy = !!job && (job.status === 'queued' || job.status === 'running')
  return { job, error, run, cancel, busy }
}

export const fmtTime = (s: number) => {
  if (!isFinite(s)) return '0:00'
  const m = Math.floor(s / 60), sec = Math.floor(s % 60), f = Math.floor((s % 1) * 10)
  return `${m}:${String(sec).padStart(2, '0')}.${f}`
}
export const fmtSize = (b: number) => b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} KB`
