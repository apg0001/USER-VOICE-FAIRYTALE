# Speech Voice Conversion Pipeline

Phase 6는 목표 발화의 길이와 timing을 유지하면서 선택한 Voice Profile을 adapter에 적용하는 Worker pipeline을 제공한다. 현재 Mock adapter는 계약과 결과 수명주기를 검증하며 실제 사람 음색을 합성하지 않는다.

## 입력부터 결과까지

```text
POST /api/files/inputs
  → 형식·크기·품질 검사와 mono PCM WAV 전처리
  → 사용자 UUID namespace에 원본·정제본 저장
POST /api/jobs (speech_voice_conversion)
  → 입력 key와 Voice Profile 소유권 검사
  → 10초 chunk별 변환·checkpoint
  → frame 수·sample rate·길이 보존 검사
  → WAV 저장과 job_outputs 기록
  → 소유자 전용 다운로드
```

입력 storage key는 `users/{internal_user_id}/inputs/` 아래에서만 유효하다. 다른 사용자가 key를 알아도 Job 생성 시 거부한다. 업로드 API는 기존 Audio Pipeline을 사용하므로 WAV/MP3/M4A/FLAC magic, ffprobe, 길이, silence/clipping과 선택적 noise reduction을 동일하게 적용한다.

## Adapter 계약

`VoiceConversionModel.convert`는 mono PCM S16LE chunk, sample rate, 모델별 Voice Profile metadata를 받고 PCM S16LE를 반환한다. pipeline은 각 chunk의 byte 길이가 입력과 같은지 확인한다. 현재 명시적으로 보존하는 속성은 다음과 같다.

- 전체 duration과 frame count
- sample rate
- chunk 내부 timing

실제 모델에서 억양·pitch·energy가 얼마나 보존되는지는 모델별 acceptance 결과에 추가해야 하며 보장하지 않은 속성을 API에 표시해서는 안 된다.

## API 예시

```bash
INPUT_KEY=$(curl -s -X POST http://localhost:8000/api/files/inputs \
  -H "X-User-ID: local-developer" \
  -F "noise_reduction=normal" \
  -F "audio_file=@speech.wav;type=audio/wav" | jq -r .input_storage_key)

curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -H "X-User-ID: local-developer" \
  -H "Idempotency-Key: speech-vc-request-0001" \
  -d "{\"mode\":\"speech_voice_conversion\",\"model_key\":\"mock-universal-v1\",\"voice_profile_id\":\"PROFILE_ID\",\"input_storage_key\":\"$INPUT_KEY\"}"
```

Worker 오류는 해당 Job을 `FAILED(INFERENCE_FAILED)`로만 전환한다. 미완료 결과 객체와 DB output row를 정리하고 모델을 unload하므로 다른 Job의 상태나 파일에 영향을 주지 않는다.
