import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { JobHistory } from './JobHistory'
import { jobFixture } from './test/fixtures'

function handlers() {
  return {
    onCancel: vi.fn(),
    onRetry: vi.fn(),
    onPreparePlayback: vi.fn(),
    onDownload: vi.fn(),
  }
}

describe('JobHistory', () => {
  it('shows an accessible empty state', () => {
    render(<JobHistory jobs={[]} playbackUrls={{}} {...handlers()} />)
    expect(screen.getByRole('heading', { name: '작업 이력' })).toBeInTheDocument()
    expect(screen.getByText('아직 생성한 작업이 없습니다.')).toBeInTheDocument()
  })

  it('shows progress, queue position and cancel action', () => {
    const callbacks = handlers()
    const queued = jobFixture('QUEUED', {
      progress: 0,
      queue_position: 3,
      estimated_remaining_seconds: 18,
    })
    render(<JobHistory jobs={[queued]} playbackUrls={{}} {...callbacks} />)

    expect(screen.getByRole('progressbar', { name: 'Text → Voice 진행률' })).toBeInTheDocument()
    expect(screen.getByText('대기 순번 3')).toBeInTheDocument()
    expect(screen.getByText('예상 18초')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Text → Voice 작업 취소' }))
    expect(callbacks.onCancel).toHaveBeenCalledWith(queued)
  })

  it('offers retry for failure and playback for completion', () => {
    const callbacks = handlers()
    const failed = jobFixture('FAILED', { user_message: 'GPU 메모리가 부족합니다.' })
    const completed = jobFixture('COMPLETED', { id: 'completed-job' })
    render(
      <JobHistory
        jobs={[failed, completed]}
        playbackUrls={{}}
        {...callbacks}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('GPU 메모리가 부족합니다.')
    fireEvent.click(screen.getByRole('button', { name: 'Text → Voice 작업 재시도' }))
    fireEvent.click(screen.getByRole('button', { name: 'Text → Voice 결과 재생 준비' }))
    fireEvent.click(screen.getByRole('button', { name: 'Text → Voice WAV 다운로드' }))
    expect(callbacks.onRetry).toHaveBeenCalledWith(failed)
    expect(callbacks.onPreparePlayback).toHaveBeenCalledWith(completed)
    expect(callbacks.onDownload).toHaveBeenCalledWith(completed)
  })
})
