export type StudioMode = 'general_tts' | 'speech_voice_conversion' | 'singing_voice_conversion'
export type JobMode = StudioMode | 'long_form_tts'
export type JobStatus = 'QUEUED' | 'PREPROCESSING' | 'LOADING_MODEL' | 'INFERENCE' | 'POSTPROCESSING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

export type ModelInfo = {
  key: string
  display_name: string
  version: string
  is_mock: boolean
  capabilities: JobMode[]
}

export type VoiceProfile = { id: string; name: string; status: string }

export type JobOutput = {
  id: string
  content_type: string
  duration_seconds: number | null
  download_url: string
}

export type JobInfo = {
  id: string
  mode: JobMode
  status: JobStatus
  progress: number
  estimated_remaining_seconds: number | null
  queue_position: number | null
  voice_profile_id: string | null
  model_key: string
  attempt: number
  retry_of_job_id: string | null
  error_code: string | null
  user_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  outputs: JobOutput[]
}

export const terminalStatuses: JobStatus[] = ['COMPLETED', 'FAILED', 'CANCELLED']

export function isTerminal(job: JobInfo): boolean {
  return terminalStatuses.includes(job.status)
}
