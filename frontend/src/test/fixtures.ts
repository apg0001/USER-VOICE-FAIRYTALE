import type { JobInfo, JobStatus } from '../types'

export function jobFixture(status: JobStatus, overrides: Partial<JobInfo> = {}): JobInfo {
  return {
    id: 'job-1',
    mode: 'general_tts',
    status,
    progress: status === 'COMPLETED' ? 100 : 45,
    estimated_remaining_seconds: null,
    queue_position: null,
    voice_profile_id: 'profile-1',
    model_key: 'mock-universal-v1',
    attempt: 1,
    retry_of_job_id: null,
    error_code: null,
    user_message: null,
    created_at: '2026-09-30T00:00:00Z',
    started_at: null,
    finished_at: status === 'COMPLETED' ? '2026-09-30T00:00:01Z' : null,
    outputs: status === 'COMPLETED' ? [{
      id: 'output-1',
      content_type: 'audio/wav',
      duration_seconds: 1,
      download_url: '/api/files/output-1',
    }] : [],
    ...overrides,
  }
}
