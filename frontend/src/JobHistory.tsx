import { isTerminal, type JobInfo } from './types'

const modeLabels: Record<JobInfo['mode'], string> = {
  general_tts: 'Text → Voice',
  long_form_tts: 'Long-form → Voice',
  speech_voice_conversion: 'Speech → Voice',
  singing_voice_conversion: 'Singing → Voice',
}

type Props = {
  jobs: JobInfo[]
  playbackUrls: Record<string, string>
  onCancel: (job: JobInfo) => void
  onRetry: (job: JobInfo) => void
  onPreparePlayback: (job: JobInfo) => void
  onDownload: (job: JobInfo) => void
}

export function JobHistory({
  jobs,
  playbackUrls,
  onCancel,
  onRetry,
  onPreparePlayback,
  onDownload,
}: Props) {
  return (
    <section className="history" aria-labelledby="history-title">
      <div className="section-heading">
        <span className="step-number">04</span>
        <div><p className="label">JOB HISTORY</p><h2 id="history-title">작업 이력</h2></div>
      </div>
      {jobs.length === 0 ? (
        <p className="empty-state">아직 생성한 작업이 없습니다.</p>
      ) : (
        <div className="job-list">
          {jobs.map((job) => {
            const output = job.outputs[0]
            return (
              <article className="job-card" key={job.id}>
                <div className="job-summary">
                  <div><strong>{modeLabels[job.mode]}</strong><small>{new Date(job.created_at).toLocaleString('ko-KR')} · 시도 {job.attempt}</small></div>
                  <span className={`job-status status-${job.status.toLowerCase()}`}>{job.status}</span>
                </div>
                {!isTerminal(job) && <progress aria-label={`${modeLabels[job.mode]} 진행률`} max="100" value={job.progress}>{job.progress}%</progress>}
                <div className="job-meta">
                  <span>진행률 {job.progress}%</span>
                  {job.queue_position !== null && <span>대기 순번 {job.queue_position}</span>}
                  {job.estimated_remaining_seconds !== null && <span>예상 {job.estimated_remaining_seconds}초</span>}
                </div>
                {(job.user_message || (job.status === 'FAILED' && job.error_code)) && <p className="job-error" role="alert">{job.user_message ?? `작업 실패 (${job.error_code})`}</p>}
                {playbackUrls[job.id] && <audio aria-label={`${modeLabels[job.mode]} 결과 재생`} controls preload="metadata" src={playbackUrls[job.id]}>오디오 재생을 지원하지 않는 브라우저입니다.</audio>}
                <div className="job-actions">
                  {!isTerminal(job) && <button type="button" aria-label={`${modeLabels[job.mode]} 작업 취소`} onClick={() => onCancel(job)}>취소</button>}
                  {['FAILED', 'CANCELLED'].includes(job.status) && <button type="button" aria-label={`${modeLabels[job.mode]} 작업 재시도`} onClick={() => onRetry(job)}>재시도</button>}
                  {job.status === 'COMPLETED' && output && !playbackUrls[job.id] && <button type="button" aria-label={`${modeLabels[job.mode]} 결과 재생 준비`} onClick={() => onPreparePlayback(job)}>재생 준비</button>}
                  {job.status === 'COMPLETED' && output && <button type="button" aria-label={`${modeLabels[job.mode]} WAV 다운로드`} onClick={() => onDownload(job)}>WAV 다운로드</button>}
                </div>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
