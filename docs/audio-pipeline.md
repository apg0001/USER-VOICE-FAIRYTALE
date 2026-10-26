# Audio Validation and Preprocessing

## 처리 순서

```text
temporary upload
  → size / extension / declared MIME
  → magic signature
  → ffprobe decode and metadata
  → ffmpeg quality analysis
  → duration / speech validation
  → original object 저장
  → optional trim / denoise / loudness normalize
  → mono, 16-bit PCM, model sampling-rate WAV
  → cleaned object 저장
```

확장자나 browser MIME만 신뢰하지 않는다. WAV/MP3/M4A/FLAC의 signature를 확인한 뒤 ffprobe가 실제 stream을 decode할 수 있어야 통과한다. subprocess는 shell 없이 argument list로 실행하며 제한 시간을 넘기면 process를 종료한다. 사용자 파일명은 storage path에 사용하지 않는다.

## 품질 리포트

`AudioQuality`은 전체 길이 외에 speech/silence 시간과 비율, peak dB, clipping, noise floor와 수준을 기록한다. 현재 speech ratio는 ffmpeg `silencedetect`, noise floor와 peak는 `astats` 기반의 사전 품질 지표다. 모델별 고급 VAD가 도입되면 동일 결과 schema를 유지한 adapter로 교체한다.

음성이 최소 기준보다 짧으면 사용자에게 재녹음을 요청한다. 내부 ffmpeg stderr와 절대 경로는 사용자 응답이나 일반 로그에 노출하지 않는다.

## 선택적 전처리

- Noise OFF: denoise를 적용하지 않음
- Noise Normal: 보수적인 `afftdn` 설정
- Noise Strong: 높은 noise floor 입력에서만 사용하도록 UI 경고 필요
- Silence trim: 시작/끝 및 긴 무음 제거
- Loudness normalization: -16 LUFS, true peak -1.5 dB 목표
- Output: mono, PCM s16le, adapter가 요청한 sample rate

과도한 denoise가 화자 음색을 손상할 수 있으므로 Normal을 기본으로 하고 원본을 항상 별도 보존한다. 변환 실패 시 이미 저장된 원본 객체를 정리해 orphan을 남기지 않는다.

## 보관과 연계

`FileService` 결과에는 원본/정제 storage key, SHA-256, probe, quality, preprocessing config가 포함된다. Phase 4의 Voice Profile API가 이 서비스를 호출하고 metadata를 DB에 기록한다. 임시 디렉터리는 context 종료 시 삭제되며 운영 cleanup 정책은 Phase 10에서 보강한다.

