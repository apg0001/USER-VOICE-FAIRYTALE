# Voice AI Platform 설계 초안

이 문서는 Issue #1에서 확정한 초기 설계다. 모델 품질과 라이선스는 실제 샘플 평가를 통과한 뒤 확정하며, 구체 모델이 플랫폼 계층을 침범하지 않게 한다.

## 1. 요구사항 정리

- Mode 1은 텍스트와 사용자 음성 샘플로 TTS를 생성한다. 일반 문장과 장문을 별도 pipeline으로 처리한다.
- Mode 2는 목표 음성/음악의 내용·타이밍·운율을 보존하면서 사용자 음색을 적용한다. 노래는 보컬 분리, SVC, 반주 재합성을 거친다.
- 장시간·GPU 작업은 비동기 Job으로 실행하고 상태, 단계 기반 진행률, ETA, Queue 위치, 취소, 재시도를 제공한다.
- 입력은 WAV/MP3/M4A/FLAC이며 검증 후 mono, 16-bit PCM WAV, 모델별 sampling rate로 변환한다.
- 원본과 정제본을 분리하고 noise reduction은 OFF/Normal/Strong으로 선택한다.
- API, 비즈니스 로직, 오디오 처리, AI 모델, Queue, Storage, DB, 설정, 로그를 분리한다.
- CPU 개발 환경은 Mock inference로 Web/API/Queue 계약을 검증한다.
- 음성 소유·사용 권한 동의와 데이터 삭제 가능성을 제품의 필수 조건으로 둔다.

## 2. 권장 시스템 아키텍처

```text
Browser
  │ HTTP / SSE
  ▼
React Frontend ───────► FastAPI
                          │ metadata / state
             ┌────────────┼────────────┐
             ▼            ▼            ▼
         PostgreSQL     Redis       ObjectStorage
                          │ queue       local → S3/MinIO
                          ▼
                     AI Worker (GPU owner)
                          │
          preprocess → model adapter → postprocess
```

API 프로세스는 CUDA 모델을 import하거나 추론하지 않는다. Worker는 하나의 GPU를 명시적으로 소유하고 `ModelManager`가 lazy loading, LRU unload, VRAM 사전 점검을 담당한다. DB는 Job 최종 상태의 기준이고 Redis 결과는 일시적 전달 수단이다.

## 3. 권장 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| Frontend | React, TypeScript, Vite | 컴포넌트 기반 UI, 타입 안전 API 연동, 빠른 개발 빌드 |
| API | Python 3.11+, FastAPI, Pydantic | 명시적 schema, async I/O, OpenAPI 자동 문서 |
| Persistence | PostgreSQL, SQLAlchemy 2, Alembic | 상태 전이와 이력의 트랜잭션, 명시적 migration |
| Queue | Celery, Redis | 취소/재시도/worker 격리 생태계와 운영 경험 |
| Media | ffmpeg/ffprobe, torchaudio(Worker) | 컨테이너 변환·검증과 tensor audio 처리 분리 |
| Storage | Local adapter → S3/MinIO | 개발 단순성, 운영 object storage 교체 가능 |
| Observability | structlog JSON, 향후 OpenTelemetry/Prometheus | request/job/model/stage 상관관계 |
| Deployment | Docker Compose, NVIDIA Container Toolkit | API와 무거운 AI image 분리, GPU 명시 할당 |

## 4. 음성 AI 후보와 선택 이유

최종 채택 전 실제 한국어 샘플, GPU, 라이선스에 대한 별도 acceptance test가 필요하다.

| 용도 | 1차 후보 | 대안 | 판단 |
|---|---|---|---|
| General/Long-form TTS | [OpenVoice V2](https://github.com/myshell-ai/OpenVoice) + base TTS | [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | OpenVoice V2는 MIT, 한국어 native support, zero-shot tone-color cloning을 명시해 상업 확장에 유리하다. CosyVoice는 한국어 zero-shot/cross-lingual과 streaming을 지원하지만 배포 의존성과 실제 라이선스 조합을 검토한다. 장문은 모델이 아니라 공통 sentence chunker/cross-fade 계층에서 해결한다. |
| Speech VC | CosyVoice VC 평가 또는 [Seed-VC](https://github.com/Plachtaa/seed-vc) | RVC | Seed-VC는 1–30초 참조 기반 zero-shot VC가 장점이지만 GPL-3.0이고 저장소가 archived 상태라 운영 기본값으로 즉시 확정하지 않는다. RVC는 품질이 높지만 보통 사용자별 학습이 필요하다. |
| Singing VC | Seed-VC SVC 평가 | [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI), [so-vits-svc](https://github.com/svc-develop-team/so-vits-svc) | 전용 SVC의 pitch/timing 보존을 평가한다. Seed-VC의 44.1 kHz SVC checkpoint는 zero-shot 장점, RVC/so-vits-svc는 학습 비용과 사용자 onboarding trade-off가 있다. |
| Source separation | BS-RoFormer 계열 평가 | [Demucs](https://github.com/facebookresearch/demucs) | 보컬 leakage, 고역 손실, 처리속도를 내부 음악 fixture로 비교한다. Demucs 공식 저장소는 archived 상태이므로 유지보수 가능한 구현/weights provenance를 확인한다. |

라이선스는 코드와 model weights를 별도로 확인한다. 특히 GPL adapter는 별도 Worker image/process로 격리해도 배포 의무가 사라지는 것이 아니므로 법률 검토 전 상용 기본 모델로 채택하지 않는다. 모델 평가표에는 한국어 MOS/화자 유사도, 발음 오류, zero/few-shot, 긴 음성 안정성, VRAM, RTF, streaming, 유지보수 상태, 코드·가중치 라이선스를 기록한다.

## 5. 전체 디렉터리 구조

```text
backend/
├─ alembic/                 # DB migration과 초기 schema
├─ app/
│  ├─ api/                  # HTTP controller/schema; 추론 코드 금지
│  ├─ audio/                # Phase 3 전처리·분리·mixing
│  ├─ core/                 # 설정, 구조화 logging, GPU 진단
│  ├─ db/                   # ORM model과 async session
│  ├─ models/               # VoiceModel adapter, registry, manager
│  ├─ pipelines/            # 작업 유형별 단계 orchestration
│  ├─ services/             # Job/Voice/File business rules
│  ├─ storage/              # Local/S3/MinIO object storage adapter
│  └─ workers/              # Celery task와 inference process
└─ tests/{unit,api,integration,model}
frontend/
├─ src/                     # Voice Studio React UI
├─ Dockerfile
└─ nginx.conf
docs/                       # architecture/security/operations 문서
models/                     # git 미추적 model cache mount
storage/                    # git 미추적 local object storage
```

아직 구현되지 않은 디렉터리는 해당 Phase 이슈에서 생성한다. 빈 구조를 미리 양산하지 않는다.

## 6. Database Schema 초안

| Table | 핵심 필드 | 책임 |
|---|---|---|
| users | id, external_id, is_active | 인증 시스템과 내부 소유권 연결 |
| voice_profiles | user_id, consent_version/at, status, metadata | 사용자 음성 논리 단위와 동의 |
| voice_samples | profile_id, original/cleaned key, 품질 지표 | 원본·정제 음성 추적 |
| jobs | mode, status, progress, model_key, input, config, metrics, error | 비동기 작업 상태의 기준 |
| job_outputs | job_id, storage_key, duration, metadata | 생성 결과와 재현 metadata |
| models | key, version, type, enabled, config | 배포 가능한 모델 catalog |

파일 binary는 DB BLOB가 아니라 Storage에 저장한다. 외래 키 삭제 정책은 profile/sample/output에는 CASCADE, profile이 삭제된 과거 Job에는 SET NULL을 적용한다. Alembic `0001`이 이 초안을 반영한다.

## 7. Job Queue 구조

```text
POST /api/jobs → DB(QUEUED) → Celery message(job_id only)
                                     │
                                     ▼
Worker claim → PREPROCESSING → LOADING_MODEL → INFERENCE
             → POSTPROCESSING → COMPLETED
                     └────────→ FAILED / CANCELLED
```

- Queue payload에는 대용량 텍스트·음성·secret을 싣지 않고 `job_id`만 전달한다.
- 허용 상태 전이는 JobService 한 곳에서 잠금과 함께 검증한다.
- Celery는 `acks_late`, worker prefetch 1, GPU worker concurrency 1을 기본으로 한다.
- progress는 단계별 범위와 모델 callback이 제공하는 세부값만 사용한다. 측정할 수 없으면 임의 숫자 대신 단계 시작값을 유지한다.
- ETA는 `(audio duration, model/version, gpu, mode)` 과거 실행의 robust processing factor로 계산하고 표본 부족 시 `null`이다.
- 취소는 DB cancel request와 Celery revoke를 결합하고 Worker가 chunk 경계에서 cooperative check한다.

## 8. GPU / Model 관리

서버 시작 시 CUDA availability/name/VRAM/CUDA/PyTorch를 기록하되 실패해도 Mock 개발 모드는 실행한다. `ModelManager`는 `(model key, version, device)`별 adapter를 보관하며 요청 시 lock, VRAM 확인, 미사용 LRU unload, load 순서로 동작한다. 한 GPU Worker는 기본 동시성 1이다.

CUDA OOM은 해당 Job을 `FAILED(GPU_OUT_OF_MEMORY)`로 기록하고 adapter 참조 제거, `gc.collect`, CUDA cache 정리 후 Worker health를 재검사한다. 같은 원인의 반복은 retry하지 않으며 다른 GPU 또는 낮은 메모리 모델로의 명시적 정책만 허용한다. 모델 loading/inference/postprocessing 시간과 peak VRAM을 기록한다.

## 9. Docker 구성

기본 `docker-compose.yml`은 frontend, backend, worker, redis, database를 실행한다. 기본 Worker는 Mock이므로 CPU에서도 개발할 수 있다. GPU 환경은 `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build`로 device reservation을 추가한다. 실제 모델 도입 Phase에서는 CUDA/PyTorch/ffmpeg가 포함된 별도 Worker Dockerfile을 만들어 API image 크기를 유지한다.

## 10. 개발 Phase

1. 기반 구조/문서/CI/Mock 계약
2. Job API, Queue, Worker 상태 전이/취소/재시도 — 구현 완료
3. 안전한 오디오 검증과 선택적 전처리 — 구현 완료
4. 동의 기반 Voice Profile과 모델별 profile builder — 구현 완료
5. 일반/장문 TTS와 결과 다운로드 — Mock 계약 구현 완료
6. Speech VC — Mock 계약 구현 완료
7. Source separation, Singing VC, mixing — Mock 계약 구현 완료
8. GPU ModelManager와 OOM recovery — CPU Mock 계약 구현 완료
9. SSE/ETA/Queue/History UX
10. 관측성, 정리, 보안, 배포/rollback

## 11. Phase별 GitHub Issue

- [#1 프로젝트 기반 구조와 실행 환경](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/1)
- [#2 비동기 Job Queue](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/2)
- [#3 오디오 검증과 전처리](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/3)
- [#4 Voice Profile과 동의](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/4)
- [#5 Text to User Voice](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/5)
- [#6 Speech Voice Conversion](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/6)
- [#7 Singing Voice Conversion](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/7)
- [#8 GPU Model Manager](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/8)
- [#9 Voice Studio UX](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/9)
- [#10 운영 안정성·보안](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/10)

각 이슈가 커지면 구현 전에 API, Worker, UI 등 독립 검증 가능한 하위 이슈로 나눈다.

## 12. 예상 기술 문제와 해결 전략

| 위험 | 전략 |
|---|---|
| 모델/weights 라이선스 불일치 | 코드와 가중치를 따로 SBOM에 기록하고 법률 검토 gate 적용 |
| 한국어 발음과 장문 일관성 | 고정 평가 corpus, 문장 chunking, punctuation-aware pause, boundary cross-fade |
| 전처리로 화자 특성 손상 | 원본 보존, 기본 Normal 이하, 지표/청취 A-B, 단계별 opt-out |
| 음악 보컬 leakage와 반주 손상 | separator 후보 benchmark, instrumental 재사용, loudness/phase 검사 |
| GPU OOM/fragmentation | GPU당 concurrency 1, VRAM admission, LRU unload, process recycling |
| 중복 실행과 상태 race | DB row lock, idempotency key, monotonic state machine, late ack |
| 장시간 작업 실패 비용 | chunk checkpoint와 재개, intermediate manifest, 원자적 output publish |
| ETA 부정확 | model/version/GPU별 실제 이력만 사용, 신뢰구간 부족 시 표시하지 않음 |
| 민감 음성 유출/사칭 | 명시적 동의, 소유권 검사, 최소 보관, 추적 삭제, audit/abuse controls |
| API/AI 의존성 결합 | adapter contract, 별도 Worker image, Mock contract test, model plugin registry |

