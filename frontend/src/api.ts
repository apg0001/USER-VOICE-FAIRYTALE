import type { JobInfo } from './types'

export const actorHeaders = { 'X-User-ID': 'local-developer' }

export async function getJob(jobId: string, signal?: AbortSignal): Promise<JobInfo> {
  const response = await fetch(`/api/jobs/${jobId}`, { headers: actorHeaders, signal })
  if (!response.ok) throw new Error('job fetch failed')
  return response.json() as Promise<JobInfo>
}

export async function listJobs(signal?: AbortSignal): Promise<JobInfo[]> {
  const response = await fetch('/api/jobs?limit=50', { headers: actorHeaders, signal })
  if (!response.ok) throw new Error('job list failed')
  const payload = await response.json() as { items: JobInfo[] }
  return payload.items
}

export async function cancelJob(jobId: string): Promise<JobInfo> {
  const response = await fetch(`/api/jobs/${jobId}/cancel`, {
    method: 'POST',
    headers: actorHeaders,
  })
  if (!response.ok) throw new Error('job cancellation failed')
  return response.json() as Promise<JobInfo>
}

export async function retryJob(jobId: string): Promise<JobInfo> {
  const response = await fetch(`/api/jobs/${jobId}/retry`, {
    method: 'POST',
    headers: actorHeaders,
  })
  if (!response.ok) throw new Error('job retry failed')
  return response.json() as Promise<JobInfo>
}

export async function fetchOutput(downloadUrl: string): Promise<Blob> {
  const response = await fetch(downloadUrl, { headers: actorHeaders })
  if (!response.ok) throw new Error('output download failed')
  return response.blob()
}
