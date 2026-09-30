import { useEffect, useState } from 'react'

type StudioMode = 'general_tts' | 'speech_voice_conversion' | 'singing_voice_conversion'

type ModelInfo = {
  key: string
  display_name: string
  version: string
  is_mock: boolean
}

const modes: Array<{ id: StudioMode; eyebrow: string; title: string; description: string }> = [
  { id: 'general_tts', eyebrow: 'TEXT', title: 'Text → Voice', description: '문장과 긴 이야기를 내 목소리로' },
  { id: 'speech_voice_conversion', eyebrow: 'SPEECH', title: 'Speech → Voice', description: '말의 흐름을 유지한 음색 변환' },
  { id: 'singing_voice_conversion', eyebrow: 'SINGING', title: 'Singing → Voice', description: '멜로디와 리듬을 살린 노래 변환' },
]

function App() {
  const [mode, setMode] = useState<StudioMode>('general_tts')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [apiStatus, setApiStatus] = useState<'checking' | 'ready' | 'offline'>('checking')

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
    ])
      .then(([, modelPayload]) => {
        setModels(modelPayload.items)
        setApiStatus('ready')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setApiStatus('offline')
      })
    return () => controller.abort()
  }, [])

  const isTextMode = mode === 'general_tts'

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
          <button className="upload-button" type="button" disabled title="Phase 4에서 활성화됩니다">
            <span>＋</span> 음성 샘플 선택
          </button>
        </div>
        <p className="consent-note">본인이 소유하거나 명시적 사용 권한을 받은 음성만 등록할 수 있습니다.</p>

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
          <textarea aria-label="변환할 텍스트" placeholder="목소리로 들려줄 이야기를 입력하세요…" maxLength={5000} />
        ) : (
          <button className="dropzone" type="button" disabled><strong>오디오 파일을 선택하세요</strong><small>WAV, MP3, M4A, FLAC · 최대 500 MB</small></button>
        )}

        <div className="options">
          <label><span><strong>Noise reduction</strong><small>배경 소음을 부드럽게 줄입니다</small></span><select defaultValue="normal"><option value="off">Off</option><option value="normal">Normal</option><option value="strong">Strong</option></select></label>
          <label><span><strong>Model</strong><small>작업에 맞는 어댑터</small></span><select disabled={models.length === 0}>{models.length ? models.map((item) => <option key={item.key}>{item.display_name} {item.is_mock ? '(Mock)' : ''}</option>) : <option>연결된 모델 없음</option>}</select></label>
        </div>

        <button className="start-button" type="button" disabled>
          작업 시작 <span>→</span>
        </button>
        <p className="phase-note">실제 음성 파일 생성은 모델 Pipeline 구현 후 활성화됩니다.</p>
      </section>

      <footer><span>VOICE FAIRY TALE</span><span>Responsible voice, thoughtfully made.</span></footer>
    </main>
  )
}

export default App

