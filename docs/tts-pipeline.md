# Text to User Voice Pipeline

Phase 5는 `general_tts`와 `long_form_tts` Job을 실제 WAV 산출물로 만든다. API는 작업을 Queue에 넣고, Worker가 Voice Profile의 모델별 metadata를 읽어 TTS adapter와 pipeline을 실행한다.

## 처리 흐름

```text
POST /api/jobs
  → QUEUED
  → PREPROCESSING
  → LOADING_MODEL
  → 문장 단위 chunking
  → chunk별 synthesize + checkpoint
  → PCM 결합과 짧은 무음 경계
  → mono 16-bit WAV 저장
  → job_outputs metadata 기록
  → COMPLETED
  → 소유자 인증 다운로드
```

일반 TTS는 하나의 큰 chunk를 허용하고, 장문 TTS는 기본 280자 이하 문장 묶음으로 분할한다. 너무 긴 단일 문장은 공백을 우선해 나누며 공백이 없으면 최대 길이에서 안전하게 분할한다. 각 chunk 완료 시 `metrics.tts_checkpoint`와 진행률을 commit하므로 향후 재시작 시 완료 chunk manifest를 재사용할 수 있는 경계가 있다.

## Adapter 계약

`TTSModel`은 `load`, `synthesize`, `unload`와 descriptor를 제공한다. `synthesize`는 mono PCM S16LE와 sample rate를 반환하며, pipeline은 모든 chunk의 sample rate가 같은지 확인한다. 실제 모델은 이 계약 뒤에 연결하고 API 프로세스가 아니라 Worker에서만 로드한다.

현재 `MockTTSModel`은 CPU와 CI에서 재현 가능한 tone WAV를 만든다. Voice Profile의 SHA fingerprint로 주파수가 달라져 프로필 전달 계약을 검증하지만 실제 사람의 음색을 복제하지 않는다. OpenVoice/CosyVoice 등 실제 adapter 채택은 한국어 품질, 코드와 weights 라이선스, VRAM/RTF 평가를 통과한 뒤 별도 model acceptance 작업으로 진행한다.

## 결과와 다운로드

Worker는 WAV를 `jobs/{job_id}/outputs` namespace에 UUID key로 저장하고 `job_outputs`에 content type, 길이, sample rate, chunk 수, model key를 기록한다. Job 응답의 `outputs[].download_url`을 통해 결과를 찾을 수 있다.

```bash
curl -H "X-User-ID: local-developer" \
  http://localhost:8000/api/files/OUTPUT_ID \
  --output result.wav
```

다운로드는 `job_outputs → jobs → users` 소유권을 확인한다. 존재하지 않거나 다른 사용자 소유인 output ID는 동일하게 `404`를 반환하며 storage key는 API에 노출하지 않는다.

## 실패와 제한

- 빈 텍스트와 200,000자 초과 입력은 API에서 거부한다.
- adapter 오류 또는 sample rate 불일치는 Job을 `FAILED(INFERENCE_FAILED)`로 종료한다.
- 결과 파일은 Job이 완료된 후에만 UI에서 다운로드한다.
- 현재 checkpoint는 진행 내역의 영속 경계이며 chunk별 오디오 재개는 후속 운영 안정화 단계에서 manifest와 함께 구현한다.
- 실제 음색 품질을 제공하는 production adapter는 아직 포함하지 않는다.
