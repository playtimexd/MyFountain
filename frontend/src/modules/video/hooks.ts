// Local copy of api/client useJob that survives React StrictMode's mount/unmount/mount cycle
// (the shared one sets alive=false in the simulated unmount and never resets it).
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, waitJob } from '../../api/client'
import type { JobInfo } from '../../types/api'

export function useJobSafe<R = any>() {
  const [job, setJob] = useState<JobInfo<R> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const run = useCallback(async (start: () => Promise<JobInfo<R>>) => {
    setError(null)
    try {
      const j0 = await start()
      return await waitJob<R>(j0, (j) => alive.current && setJob(j))
    } catch (e: any) {
      if (alive.current) setError(e.message || String(e))
      throw e
    }
  }, [])
  const cancel = useCallback(() => { if (job) void api.post(`/api/jobs/${job.id}/cancel`) }, [job])
  const busy = !!job && (job.status === 'queued' || job.status === 'running')
  return { job, error, run, cancel, busy }
}
