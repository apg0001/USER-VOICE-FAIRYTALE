import { useEffect, useRef, useState } from 'react'

import { monitorJob } from './jobMonitor'
import type { JobInfo } from './types'

export function useJobMonitor(
  jobId: string | null,
  onJob: (job: JobInfo) => void,
): 'idle' | 'sse' | 'polling' | 'offline' {
  const [transport, setTransport] = useState<'idle' | 'sse' | 'polling' | 'offline'>('idle')
  const onJobRef = useRef(onJob)

  useEffect(() => {
    onJobRef.current = onJob
  }, [onJob])

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()
    void monitorJob({
      jobId,
      signal: controller.signal,
      onJob: (job) => onJobRef.current(job),
      onTransport: setTransport,
    }).catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        setTransport('offline')
      }
    })
    return () => controller.abort()
  }, [jobId])

  return jobId ? transport : 'idle'
}
