import { useEffect, useState } from 'react'

type StudioMode = 'general_tts' | 'speech_voice_conversion' | 'singing_voice_conversion'

type ModelInfo = {
  key: string
  display_name: string
  version: string
  is_mock: boolean
}

type VoiceProfile = { id: string; name: string; status: string }
type JobOutput = { id: string; download_url: string }
type JobInfo = { id: string; status: string; progress: number; outputs: JobOutput[] }

const modes: Array<{ id: StudioMode; eyebrow: string; title: string; description: string }> = [
  { id: 'general_tts', eyebrow: 'TEXT', title: 'Text → Voice', description: '문장과 긴 이야기를 내 목소리로' },
  { id: 'speech_voice_conversion', eyebrow: 'SPEECH', title: 'Speech → Voice', description: '말의 흐름을 유지한 음색 변환' },
  { id: 'singing_voice_conversion', eyebrow: 'SINGING', title: 'Singing → Voice', description: '멜로디와 리듬을 살린 노래 변환' },
]

function App() {
  const [mode, setMode] = useState<StudioMode>('general_tts')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [apiStatus, setApiStatus] = useState<'checking' | 'ready' | 'offline'>('checking')
  const [voiceFile, setVoiceFile] = useState<File | null>(null)
  const [profileName, setProfileName] = useState('내 이야기 목소리')
  const [consentAccepted, setConsentAccepted] = useState(false)
  const [ownershipDeclared, setOwnershipDeclared] = useState(false)
  const [consentVersion, setConsentVersion] = useState('')
  const [profileState, setProfileState] = useState<'idle' | 'uploading' | 'ready' | 'error'>('idle')
  const [profiles, setProfiles] = useState<VoiceProfile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState('')
  const [text, setText] = useState('')
  const [job, setJob] = useState<JobInfo | null>(null)
  const [jobError, setJobError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetch('/api/health', { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error('API unavailable')
        return response.json()
      }),
      fetch('/api/models', { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error('Models unavailable')
        return response.json() as Promise<{ items: ModelInfo[] }>
      }),
      fetch('/api/voices/consent', { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error('Consent unavailable')
        return response.json() as Promise<{ version: string }>
      }),
      fetch('/api/voices', {
        signal: controller.signal,
        headers: { 'X-User-ID': 'local-developer' },
      }).then((response) => {
        if (!response.ok) throw new Error('Profiles unavailable')
        return response.json() as Promise<{ items: VoiceProfile[] }>
      }),
    ])
      .then(([, modelPayload, consentPayload, profilePayload]) => {
        setModels(modelPayload.items)
        setConsentVersion(consentPayload.version)
        const readyProfiles = profilePayload.items.filter((profile) => profile.status === 'READY')
        setProfiles(readyProfiles)
        setSelectedProfileId(readyProfiles[0]?.id ?? '')
        setApiStatus('ready')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setApiStatus('offline')
      })
    return () => controller.abort()
  }, [])

  const isTextMode = mode === 'general_tts'

  const registerVoice = async () => {
    if (!voiceFile || !consentAccepted || !ownershipDeclared || !profileName.trim()) return
    setProfileState('uploading')
    const form = new FormData()
    form.append('name', profileName.trim())
    form.append('consent_accepted', 'true')
    form.append('owns_voice_or_has_permission', 'true')
    form.append('consent_version', consentVersion)
    form.append('noise_reduction', 'normal')
    form.append('voice_sample', voiceFile)
    try {
      const response = await fetch('/api/voices', {
        method: 'POST',
        headers: { 'X-User-ID': 'local-developer' },
        body: form,
      })
      if (!response.ok) throw new Error('voice profile upload failed')
      const created = await response.json() as VoiceProfile
      setProfiles((current) => [created, ...current])
      setSelectedProfileId(created.id)
      setProfileState('ready')
    } catch {
      setProfileState('error')
    }
  }

  const startTts = async () => {
    if (!text.trim() || !selectedProfileId || models.length === 0) return
    setJobError('')
    setJob(null)
    try {
      const response = await fetch('/api/jobs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': 'local-developer',
          'Idempotency-Key': crypto.randomUUID(),
        },
        body: JSON.stringify({
          mode: text.length > 500 ? 'long_form_tts' : 'general_tts',
          model_key: models[0].key,
          voice_profile_id: selectedProfileId,
          input_text: text.trim(),
          request_config: {},
        }),
      })
      if (!response.ok) throw new Error('job creation failed')
      let current = await response.json() as JobInfo
      setJob(current)
      for (let attempt = 0; attempt < 150 && !['COMPLETED', 'FAILED', 'CANCELLED'].includes(current.status); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        const polled = await fetch(`/api/jobs/${current.id}`, {
          headers: { 'X-User-ID': 'local-developer' },
        })
        if (!polled.ok) throw new Error('job polling failed')
        current = await polled.json() as JobInfo
        setJob(current)
      }
      if (current.status !== 'COMPLETED') throw new Error('job did not complete')
    } catch {
      setJobError('작업을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.')
    }
  }

  const downloadOutput = async (output: JobOutput) => {
    const response = await fetch(output.download_url, {
      headers: { 'X-User-ID': 'local-developer' },
    })
    if (!response.ok) {
      setJobError('결과 파일을 다운로드하지 못했습니다.')
      return
    }
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `voice-${output.id}.wav`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#studio" aria-label="Voice Fairy Tale 홈">
          <span className="brand-mark" aria-hidden="true">V</span>
          <span><strong>Voice</strong> Fairy Tale</span>
        </a>
        <div className={`system-state ${apiStatus}`}>
          <span className="state-dot" />
          {apiStatus === 'ready' ? 'Studio ready' : apiStatus === 'offline' ? 'API offline' : 'Connecting'}
        </div>
      </header>

      <section className="hero" id="studio">
        <p className="kicker">VOICE AI STUDIO · JOB SYSTEM</p>
        <h1>당신의 목소리로,<br /><em>새로운 이야기를.</em></h1>
        <p className="hero-copy">한 번의 음성 등록으로 낭독, 대화, 노래까지.<br />작업에 맞는 모델을 안전하게 연결하는 Voice AI 플랫폼입니다.</p>
      </section>

      <section className="studio-card" aria-label="음성 작업 설정">
        <div className="step-row">
          <span className="step-number">01</span>
          <div><p className="label">VOICE PROFILE</p><h2>내 음성 등록</h2></div>
          <label className="upload-button">
            <span>＋</span> {voiceFile ? voiceFile.name : '음성 샘플 선택'}
            <input className="visually-hidden" type="file" accept=".wav,.mp3,.m4a,.flac,audio/*" onChange={(event) => setVoiceFile(event.target.files?.[0] ?? null)} />
          </label>
        </div>
        <p className="consent-note">본인이 소유하거나 명시적 사용 권한을 받은 음성만 등록할 수 있습니다.</p>
        <div className="profile-form">
          <input aria-label="음성 프로필 이름" value={profileName} maxLength={120} onChange={(event) => setProfileName(event.target.value)} />
          <label><input type="checkbox" checked={consentAccepted} onChange={(event) => setConsentAccepted(event.target.checked)} /> 음성 처리 및 Voice Profile 생성에 동의합니다.</label>
          <label><input type="checkbox" checked={ownershipDeclared} onChange={(event) => setOwnershipDeclared(event.target.checked)} /> 본인의 음성이거나 명시적 사용 권한이 있습니다.</label>
          <button type="button" onClick={registerVoice} disabled={!voiceFile || !consentAccepted || !ownershipDeclared || !consentVersion || profileState === 'uploading'}>{profileState === 'uploading' ? '검증 및 등록 중…' : 'Voice Profile 등록'}</button>
          {profileState === 'ready' && <small className="profile-success">음성 품질 검증과 등록이 완료되었습니다.</small>}
          {profileState === 'error' && <small className="profile-error">등록하지 못했습니다. 파일 품질과 서버 상태를 확인해 주세요.</small>}
        </div>

        <div className="divider" />

        <div className="section-heading"><span className="step-number">02</span><div><p className="label">WORK TYPE</p><h2>작업 유형</h2></div></div>
        <div className="mode-grid">
          {modes.map((item) => (
            <button key={item.id} className={`mode-card ${mode === item.id ? 'selected' : ''}`} onClick={() => setMode(item.id)} type="button">
              <span className="mode-eyebrow">{item.eyebrow}</span>
              <strong>{item.title}</strong>
              <small>{item.description}</small>
              <span className="radio" aria-hidden="true" />
            </button>
          ))}
        </div>

        <div className="divider" />

        <div className="section-heading"><span className="step-number">03</span><div><p className="label">INPUT</p><h2>{isTextMode ? '텍스트 입력' : '오디오 입력'}</h2></div></div>
        {isTextMode ? (
          <textarea aria-label="변환할 텍스트" placeholder="목소리로 들려줄 이야기를 입력하세요…" maxLength={200000} value={text} onChange={(event) => setText(event.target.value)} />
        ) : (
          <button className="dropzone" type="button" disabled><strong>오디오 파일을 선택하세요</strong><small>WAV, MP3, M4A, FLAC · 최대 500 MB</small></button>
        )}

        <div className="options">
          <label><span><strong>Voice Profile</strong><small>사용할 등록 음성</small></span><select value={selectedProfileId} onChange={(event) => setSelectedProfileId(event.target.value)} disabled={profiles.length === 0}>{profiles.length ? profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>) : <option value="">등록된 음성 없음</option>}</select></label>
          <label><span><strong>Noise reduction</strong><small>배경 소음을 부드럽게 줄입니다</small></span><select defaultValue="normal"><option value="off">Off</option><option value="normal">Normal</option><option value="strong">Strong</option></select></label>
          <label><span><strong>Model</strong><small>작업에 맞는 어댑터</small></span><select disabled={models.length === 0}>{models.length ? models.map((item) => <option key={item.key}>{item.display_name} {item.is_mock ? '(Mock)' : ''}</option>) : <option>연결된 모델 없음</option>}</select></label>
        </div>

        <button className="start-button" type="button" onClick={startTts} disabled={!isTextMode || !text.trim() || !selectedProfileId || !models.length || (job !== null && !['COMPLETED', 'FAILED', 'CANCELLED'].includes(job.status))}>
          {job && !['COMPLETED', 'FAILED', 'CANCELLED'].includes(job.status) ? `${job.status} · ${job.progress}%` : '작업 시작'} <span>→</span>
        </button>
        {job?.status === 'COMPLETED' && job.outputs[0] && <button className="download-button" type="button" onClick={() => downloadOutput(job.outputs[0])}>WAV 결과 다운로드</button>}
        {jobError && <p className="profile-error phase-note">{jobError}</p>}
        <p className="phase-note">500자를 초과하는 텍스트는 장문 TTS로 자동 분할해 처리합니다.</p>
      </section>

      <footer><span>VOICE FAIRY TALE</span><span>Responsible voice, thoughtfully made.</span></footer>
    </main>
  )
}

export default App

