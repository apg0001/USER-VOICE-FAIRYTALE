import { actorHeaders, getJob } from './api'
import { isTerminal, type JobInfo } from './types'

type Fetcher = typeof fetch
type Transport = 'sse' | 'polling' | 'offline'

type MonitorOptions = {
  jobId: string
  signal: AbortSignal
  onJob: (job: JobInfo) => void
  onTransport?: (transport: Transport) => void
  fetcher?: Fetcher
  maxReconnects?: number
  reconnectDelayMs?: number
  pollIntervalMs?: number
}

type StreamResult = { lastEventId: string; terminal: boolean }

function parseEvent(frame: string): { id?: string; event?: string; data?: string } {
  const parsed: { id?: string; event?: string; data?: string } = {}
  const data: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('id:')) parsed.id = line.slice(3).trim()
    if (line.startsWith('event:')) parsed.event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (data.length) parsed.data = data.join('\n')
  return parsed
}

async function wait(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0) return
  await new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeout)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timeout = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export async function consumeJobEvents(
  jobId: string,
  signal: AbortSignal,
  onJob: (job: JobInfo) => void,
  fetcher: Fetcher = fetch,
  lastEventId = '',
): Promise<StreamResult> {
  const headers: Record<string, string> = { ...actorHeaders, Accept: 'text/event-stream' }
  if (lastEventId) headers['Last-Event-ID'] = lastEventId
  const response = await fetcher(`/api/jobs/${jobId}/events`, { headers, signal })
  if (!response.ok || !response.body) throw new Error('job event stream unavailable')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let cursor = lastEventId
  let terminal = false

  while (!signal.aborted) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const event = parseEvent(frame)
      if (event.id) cursor = event.id
      if (event.event === 'error') throw new Error('job event stream ended with error')
      if (event.event !== 'job' || !event.data) continue
      const job = JSON.parse(event.data) as JobInfo
      onJob(job)
      if (isTerminal(job)) terminal = true
    }
    if (done || terminal) break
  }
  return { lastEventId: cursor, terminal }
}

export async function monitorJob(options: MonitorOptions): Promise<void> {
  const {
    jobId,
    signal,
    onJob,
    onTransport,
    fetcher = fetch,
    maxReconnects = 2,
    reconnectDelayMs = 750,
    pollIntervalMs = 1_000,
  } = options
  let lastEventId = ''
  let currentTransport: Transport | null = null
  const reportTransport = (transport: Transport) => {
    if (currentTransport !== transport) {
      currentTransport = transport
      onTransport?.(transport)
    }
  }
  reportTransport('sse')
  for (let attempt = 0; attempt <= maxReconnects && !signal.aborted; attempt += 1) {
    try {
      const result = await consumeJobEvents(
        jobId, signal, onJob, fetcher, lastEventId,
      )
      lastEventId = result.lastEventId
      if (result.terminal) return
    } catch {
      if (signal.aborted) return
      if (attempt === maxReconnects) break
    }
    await wait(reconnectDelayMs, signal)
  }

  reportTransport('polling')
  while (!signal.aborted) {
    try {
      reportTransport('polling')
      const job = fetcher === fetch
        ? await getJob(jobId, signal)
        : await fetcher(`/api/jobs/${jobId}`, { headers: actorHeaders, signal }).then(
            async (response) => {
              if (!response.ok) throw new Error('job polling failed')
              return response.json() as Promise<JobInfo>
            },
          )
      onJob(job)
      if (isTerminal(job)) return
    } catch {
      if (signal.aborted) return
      reportTransport('offline')
    }
    await wait(pollIntervalMs, signal)
  }
}
