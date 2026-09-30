import { describe, expect, it, vi } from 'vitest'

import { consumeJobEvents, monitorJob } from './jobMonitor'
import type { JobInfo } from './types'
import { jobFixture } from './test/fixtures'

function eventResponse(job: JobInfo, id: number): Response {
  return new Response(
    `retry: 2000\n\nid: ${id}\nevent: job\ndata: ${JSON.stringify(job)}\n\n`,
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  )
}

describe('job event monitor', () => {
  it('parses a terminal SSE snapshot', async () => {
    const completed = jobFixture('COMPLETED')
    const received: JobInfo[] = []
    const fetcher = vi.fn(async () => eventResponse(completed, 7)) as unknown as typeof fetch

    const result = await consumeJobEvents(
      completed.id,
      new AbortController().signal,
      (job) => received.push(job),
      fetcher,
    )

    expect(result).toEqual({ lastEventId: '7', terminal: true })
    expect(received).toEqual([completed])
  })

  it('reconnects with the last event id before falling back', async () => {
    const running = jobFixture('INFERENCE')
    const completed = jobFixture('COMPLETED')
    const requests: Array<RequestInit | undefined> = []
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(init)
      return requests.length === 1 ? eventResponse(running, 5) : eventResponse(completed, 6)
    }) as unknown as typeof fetch
    const received: JobInfo[] = []

    await monitorJob({
      jobId: running.id,
      signal: new AbortController().signal,
      onJob: (job) => received.push(job),
      fetcher,
      reconnectDelayMs: 0,
    })

    expect(received.map((job) => job.status)).toEqual(['INFERENCE', 'COMPLETED'])
    expect(new Headers(requests[1]?.headers).get('Last-Event-ID')).toBe('5')
  })

  it('uses polling when the event stream is unavailable', async () => {
    const completed = jobFixture('COMPLETED')
    const transports: string[] = []
    let callCount = 0
    const fetcher = vi.fn(async () => {
      callCount += 1
      return callCount === 1
        ? new Response(null, { status: 503 })
        : new Response(JSON.stringify(completed), { status: 200 })
    }) as unknown as typeof fetch

    await monitorJob({
      jobId: completed.id,
      signal: new AbortController().signal,
      onJob: vi.fn(),
      onTransport: (transport) => transports.push(transport),
      fetcher,
      maxReconnects: 0,
      pollIntervalMs: 0,
    })

    expect(transports).toEqual(['sse', 'polling'])
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
