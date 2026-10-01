# Voice Fairy Tale

사용자가 동의하여 등록한 음색으로 텍스트, 말, 노래를 변환하는 확장 가능한 Voice AI Platform입니다. API 서버와 GPU 추론 Worker를 분리하고, 장시간 작업을 Queue 기반 Job으로 관리하는 것을 핵심 원칙으로 삼습니다.

> 현재 범위: **Phase 11 실제 한국어 TTS**. 기존 Mock adapter와 함께, 고정된 Fun-CosyVoice3 0.5B 체크포인트를 워커 전용 실제 adapter로 선택할 수 있습니다. RTX 3050 8GB에서 한국어 고정 코퍼스 acceptance를 통과했으며 API 이미지는 무거운 추론 의존성을 포함하지 않습니다.

## 주요 기능

- Text → User Voice: 일반 문장과 장문 TTS
- Speech → User Voice: 내용·타이밍·운율을 가능한 범위에서 보존하는 음색 변환
- Singing → User Voice: 보컬 분리, 전용 SVC, 기존 반주와 재합성
- 음성 품질 검증, 선택적 noise reduction, Voice Profile 관리
- 비동기 Job, 진행률, ETA, Queue 위치, 취소·재시도
- 교체 가능한 모델 adapter와 GPU lifecycle 관리
- DB 추적 보존기간, orphan reconciliation, 사용자 전체 데이터 삭제
- 민감정보 redaction, Prometheus 지표, readiness와 trusted proxy 인증 경계

현재 구현 여부는 [개발 단계](#개발-단계)와 GitHub Issue #1–#10을 기준으로 확인합니다.

## System Architecture

```text
Browser → React/Nginx → FastAPI → PostgreSQL
                         │  └──→ Object Storage
                         └─────→ Redis Queue → AI Worker → GPU/Models
```

- FastAPI는 인증, schema, Job/파일 metadata만 처리하며 무거운 inference를 실행하지 않습니다.
- AI Worker가 전처리, model adapter, 후처리를 실행합니다.
- 개발/CI에서는 실제 GPU 모델 대신 동일한 interface의 Mock adapter를 사용합니다.
- 자세한 결정과 모델 비교는 [설계 문서](docs/architecture.md)를 참조하세요.

## Processing Pipeline

```text
Upload → validate → resample/channel conversion → optional trim/normalize/denoise/VAD
       → model-specific preprocess → inference → postprocess → validate → storage
```

노래 작업은 `source separation → vocal SVC → loudness alignment → instrumental mixing` 단계를 추가합니다. 전처리는 입력과 목적에 따라 선택하며 원본 음성을 덮어쓰지 않습니다.

## Project Structure

```text
.
├─ backend/
│  ├─ alembic/             # PostgreSQL schema migration
│  ├─ app/
│  │  ├─ api/              # FastAPI route와 response schema
│  │  ├─ audio/            # magic 검증, ffmpeg adapter, 전처리 pipeline
│  │  ├─ core/             # 환경 설정, JSON logging, lazy GPU 진단
│  │  ├─ db/               # SQLAlchemy model/session
│  │  ├─ models/           # VoiceModel, Mock adapter, registry
│  │  ├─ pipelines/        # TTS 등 작업 유형별 orchestration
│  │  ├─ profiles/         # 모델별 Voice Profile builder와 registry
│  │  ├─ queue/            # Celery를 감싸는 JobQueue 계약
│  │  ├─ services/         # Job 상태 전이와 application rule
│  │  ├─ safety/           # provenance, watermark/abuse extension port
│  │  ├─ storage/          # ObjectStorage와 안전한 local adapter
│  │  └─ workers/          # Celery app와 inference task 경계
│  └─ tests/{api,unit}/     # GPU가 필요 없는 테스트
├─ frontend/
│  ├─ src/                 # React Voice Studio
│  ├─ nginx.conf           # SPA와 /api reverse proxy
│  └─ Dockerfile
├─ docs/
│  ├─ agent-handoff.md      # 후속 에이전트용 현재 상태와 실행 체크리스트
│  ├─ architecture.md      # 12개 초기 설계 산출물
│  ├─ voice-profiles.md     # 동의, 소유권, 저장·삭제 수명주기
│  ├─ tts-pipeline.md       # 일반·장문 TTS와 결과 다운로드 계약
│  ├─ speech-voice-conversion.md # 발화 변환과 보존 속성 계약
│  ├─ singing-voice-conversion.md # 보컬 분리·SVC·재합성 계약
│  ├─ model-manager.md      # GPU 진단, model lease/LRU/OOM 복구 계약
│  ├─ studio-job-ux.md      # SSE와 Voice Studio 작업 UX 계약
│  ├─ operations.md         # 배포·관측·보존·복구 runbook
│  └─ security.md          # 동의·업로드·삭제 원칙
├─ docker-compose.yml      # CPU/Mock 개발 stack
├─ docker-compose.gpu.yml  # NVIDIA device override
└─ .github/workflows/ci.yml
```

## Directory / File Description

- `backend/app/main.py`: application factory, CORS, request ID, lifecycle
- `backend/app/core/config.py`: 환경 변수의 단일 typed source
- `backend/app/core/gpu.py`: PyTorch를 지연 import하는 CUDA/VRAM 진단 port
- `backend/app/db/models.py`: users, profiles, samples, jobs, outputs, models
- `backend/app/models/base.py`: 모든 AI adapter가 지켜야 하는 lifecycle 계약
- `backend/app/models/registry.py`: 모델 key/capability별 동적 선택
- `backend/app/models/manager.py`: model lease, lifecycle lock, LRU와 VRAM admission
- `backend/app/models/tts/`: TTS 전용 adapter 계약, registry, Mock WAV 모델
- `backend/app/models/voice_conversion/`: Speech VC adapter 계약과 Mock 모델
- `backend/app/models/separation/`: 보컬/반주 분리 adapter 계약과 Mock 모델
- `backend/app/models/singing/`: Singing VC adapter 계약과 Mock 모델
- `backend/app/audio/mixing.py`: 보컬 RMS 정렬, mixing, peak limiter
- `backend/app/pipelines/tts.py`: 문장 chunking, checkpoint, WAV 결합·저장
- `backend/app/pipelines/voice_conversion.py`: 발화 chunk 변환과 timing 검증
- `backend/app/pipelines/singing.py`: 분리→SVC→재합성, manifest와 중간물 수명주기
- `backend/app/profiles/registry.py`: 모델별 Voice Profile builder 선택과 실행
- `backend/app/services/job_service.py`: 멱등 생성, 상태 전이, 취소·재시도, ETA
- `backend/app/services/voice_service.py`: 동의 우선 검증, 등록·소유권·삭제 수명주기
- `backend/app/services/file_service.py`: 원본/정제본을 분리하는 안전한 ingest orchestration
- `backend/app/services/cleanup_service.py`: 만료·pending 삭제 retry와 orphan reconciliation
- `backend/app/core/metrics.py`: bounded-label Prometheus metric registry
- `backend/app/audio/ffmpeg.py`: shell을 사용하지 않는 ffprobe/ffmpeg 실행과 품질 분석
- `backend/app/queue/`: API 테스트와 Celery를 분리하는 Queue port/adapter
- `backend/app/storage/local.py`: UUID key와 경로 순회 방어를 갖춘 개발 저장소
- `backend/app/workers/inference_worker.py`: API와 inference 프로세스의 경계
- `backend/app/workers/model_runtime.py`: Worker process 범위 ModelManager singleton
- `backend/app/workers/gpu_check.py`: 실제 GPU runner 사전 점검 명령
- `backend/app/api/jobs/events.py`: 짧은 DB session을 사용하는 소유자 전용 SSE
- `frontend/src/App.tsx`: Voice Studio orchestration과 작업 생성
- `frontend/src/jobMonitor.ts`: SSE parser/reconnect와 polling fallback
- `frontend/src/JobHistory.tsx`: 작업 상태·제어·audio player UI

파일이나 책임이 바뀌면 이 목록과 `docs/architecture.md`를 같은 commit에서 갱신합니다.

새 대화나 별도 에이전트가 개발을 이어갈 때는 먼저 [후속 에이전트 인수인계 문서](docs/agent-handoff.md)를 읽습니다.

## Model Architecture

모델은 아래 interface를 구현하는 adapter로 연결합니다.

```python
class VoiceModel:
    def load(self): ...
    def preprocess(self, input_data): ...
    def infer(self, input_data, voice_profile): ...
    def postprocess(self, output): ...
    def unload(self): ...
```

Registry는 구체 라이브러리 대신 stable model key와 capability를 노출합니다. 초기 후보는 OpenVoice V2/CosyVoice(TTS·VC), Seed-VC/RVC/so-vits-svc(SVC), BS-RoFormer/Demucs(분리)이며 확정이 아닙니다. 라이선스, 한국어 평가, 화자 유사도, VRAM, RTF와 유지보수 상태를 통과해야 실제 adapter로 등록합니다.

## API Server

구동 후 OpenAPI는 `http://localhost:8000/api/docs`에서 확인합니다.

| Method | Path | 현재 상태 | 설명 |
|---|---|---|---|
| GET | `/api/health` | 구현 | process liveness와 version |
| GET | `/api/ready` | 구현 | DB·storage·선택적 Redis readiness |
| GET | `/api/metrics` | 구현 | Prometheus 호환 요청·Job·삭제 지표 |
| GET | `/api/models` | 구현 | capability별 등록 모델 |
| GET | `/api/voices/consent` | 구현 | 현재 동의문과 버전 |
| POST/GET | `/api/voices` | 구현 | 동의 기반 Voice Profile 등록·목록 |
| GET/DELETE | `/api/voices/{id}` | 구현 | 소유자 범위 조회·추적 삭제 |
| POST | `/api/files/inputs` | 구현 | 소유자 namespace 입력 검증·전처리; `speech`/`singing` preset |
| POST/GET | `/api/jobs` | 구현 | 멱등 작업 생성·목록 |
| GET | `/api/jobs/{id}` | 구현 | 소유자 범위 상태·진행률 조회 |
| GET | `/api/jobs/{id}/events` | 구현 | cursor·heartbeat를 포함한 소유자 전용 SSE |
| POST | `/api/jobs/{id}/cancel` | 구현 | cooperative cancel |
| POST | `/api/jobs/{id}/retry` | 구현 | 실패·취소 작업 재시도 |
| GET | `/api/files/{id}` | 구현 | Job 소유권 검사 후 WAV 결과 stream |
| DELETE | `/api/users/me` | 구현 | 활성 Job 취소와 소유 데이터 전체 추적 삭제 |

아직 구현되지 않은 endpoint를 빈 성공 응답으로 제공하지 않습니다.

## AI Worker

Celery Worker는 API와 별도 프로세스입니다. `voice.run_inference`는 DB에서 Job을 읽고 각 단계 상태를 commit하며 모델 lifecycle을 끝까지 실행합니다. 기본 환경의 Mock은 유효한 WAV를 생성하지만 실제 음색 품질 모델은 아닙니다. 실제 Fun-CosyVoice3 adapter와 PyTorch/CUDA 의존성은 `Dockerfile.worker-cosyvoice`로 만든 전용 Worker image에만 설치됩니다.

## Queue

Redis는 broker이고 PostgreSQL의 `jobs`가 영속 상태의 기준입니다. 기본 Worker 설정은 late ack, prefetch 1, concurrency 1입니다. 상태는 `QUEUED → PREPROCESSING → LOADING_MODEL → INFERENCE → POSTPROCESSING → COMPLETED`이며 어느 단계에서든 `FAILED` 또는 협력적 `CANCELLED`로 종료할 수 있습니다.

생성 요청은 `Idempotency-Key`로 중복 dispatch를 차단합니다. 진행률은 상태별 허용 범위 안에서 단조 증가해야 하며 ETA는 동일 model/mode/GPU 실행 이력이 3개 이상일 때만 계산합니다. 상세 계약은 [Job System 문서](docs/job-system.md)에 있습니다.

## GPU / CUDA

기본 compose는 GPU 없이 Mock으로 실행됩니다. GPU가 있는 Linux host에서는 NVIDIA Container Toolkit 설치 후 다음 override를 사용합니다.

```bash
python -m pip install huggingface-hub==0.36.0
python scripts/download_cosyvoice3.py
python scripts/verify_cosyvoice3.py
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Worker 시작 시 Mock mode는 PyTorch를 import하지 않고 device 설정만 기록합니다. 실제 inference mode는 CUDA availability, device name, total/free VRAM, PyTorch/CUDA version을 진단합니다. ModelManager는 model lease가 해제된 idle entry만 LRU eviction하며, CUDA OOM이면 현재 Job을 `CUDA_OOM`으로 실패시키고 전체 idle cache, Python GC, CUDA cache를 정리합니다. 자세한 계약과 실제 GPU 검증 절차는 [Model Manager 문서](docs/model-manager.md)에 있습니다.

실제 한국어 TTS는 `ENABLE_COSYVOICE3=true`일 때만 등록됩니다. 체크포인트를 먼저 내려받은 뒤 GPU compose override를 사용하세요. 평가 기준과 측정치는 [모델 평가 문서](docs/model-evaluation.md)에 있습니다.

## Docker

서비스는 `frontend`, `backend`, `worker`, `scheduler`, `redis`, `database`입니다. scheduler는 15분마다 보존기간 cleanup을 실행합니다. 기본 compose는 미디어와 모델을 named volume에 저장하고, GPU override는 검증된 로컬 `models/` checkpoint를 read-only bind mount합니다. DB와 Redis는 health check 뒤에 의존 서비스를 시작합니다.

Docker Desktop 또는 Docker Engine이 설치된 환경에서:

```bash
cp .env.example .env
docker compose up --build
```

- Studio: <http://localhost:3000>
- API docs: <http://localhost:8000/api/docs>
- 종료: `docker compose down`

운영 환경에서는 `.env.example`의 기본 비밀번호를 절대 사용하지 말고 secret manager 값을 주입하세요.

## Installation

필수 도구는 Python 3.11+, Node.js 22+, ffmpeg입니다.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e "./backend[dev]"
cd frontend && npm ci
```

## Environment Variables

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | local SQLite | async SQLAlchemy URL; compose는 PostgreSQL 사용 |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker/backend |
| `STORAGE_PATH` | `./storage` | Local object root |
| `MODEL_PATH` | `./models` | 모델 cache root |
| `CUDA_DEVICE` | `cuda:0` | Worker device |
| `MAX_UPLOAD_SIZE` | 500 MiB | 서버측 상한 |
| `MODEL_CACHE_LIMIT` | 2 | Worker에 동시에 유지할 모델 수; Singing은 최소 2 필요 |
| `GPU_VRAM_RESERVE_MB` | 512 | model admission 후 남겨 둘 VRAM 안전 여유 |
| `SSE_POLL_INTERVAL_SECONDS` | 1 | SSE가 DB 상태를 다시 읽는 간격 |
| `SSE_HEARTBEAT_SECONDS` | 15 | proxy idle timeout 방지 heartbeat 간격 |
| `AUTH_MODE` | `development_header` | 개발 header 또는 운영 trusted proxy |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | 120 | API process별 요청 방어 한도 |
| `READINESS_REQUIRE_REDIS` | false | readiness Redis ping 필수 여부 |
| `USE_MOCK_INFERENCE` | `true` | 개발/CI model registry |
| `ENABLE_COSYVOICE3` | `false` | CosyVoice3 descriptor/profile/worker adapter 활성화 |
| `COSYVOICE_RUNTIME_PATH` | `./models/cosyvoice-runtime` | 고정 CosyVoice runtime checkout |
| `COSYVOICE_CHECKPOINT_PATH` | `./models/cosyvoice3-0.5b-2512` | 매니페스트가 포함된 고정 checkpoint |
| `COSYVOICE_FP16` | `true` | 실제 Worker의 FP16 추론 |
| `TEMP_RETENTION_HOURS` | 24 | 디버그 임시 파일 최대 보존 |
| `INPUT_RETENTION_HOURS` | 24 | 입력 원본·정제본 보존시간 |
| `OUTPUT_RETENTION_HOURS` | 168 | 생성 결과 보존시간 |
| `CLEANUP_BATCH_SIZE` | 100 | cleanup 종류별 batch 상한 |
| `ORPHAN_GRACE_HOURS` | 2 | orphan 판정 전 commit 유예시간 |
| `VOICE_CONSENT_VERSION` | `2026-09-01` | 등록 시 요구하는 동의문 버전 |
| `MIN_VOICE_PROFILE_SPEECH_SECONDS` | 10 | 프로필 생성에 필요한 유효 발화 길이 |

전체 값은 [.env.example](.env.example)에 있습니다. 실제 `.env`는 commit하지 않습니다.

## Running

두 터미널에서 로컬 개발 서버를 실행할 수 있습니다.

```bash
cd backend
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
cd frontend
npm run dev
```

검증:

```bash
cd backend
python -m ruff check app tests
python -m mypy app
python -m pytest

cd ../frontend
npm run lint
npm test
npm run build
```

## API

예시:

```bash
curl http://localhost:8000/api/health
curl "http://localhost:8000/api/models?capability=general_tts"
curl http://localhost:8000/api/voices/consent
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -H "X-User-ID: local-developer" \
  -H "Idempotency-Key: example-request-0001" \
  -d '{"mode":"general_tts","model_key":"mock-universal-v1","input_text":"안녕하세요"}'
```

모든 응답에는 추적 가능한 `X-Request-ID`가 포함됩니다. 사용자 오류와 내부 debug 정보는 후속 API에서 분리하며 원본 민감 데이터는 로그에 기록하지 않습니다.

## 개발 단계

- [x] Phase 1: repository, API/UI/DB/Docker/문서/CI 기반
- [x] Phase 2: Job 생성, 상태 전이, Queue, 진행률, 취소, retry
- [x] Phase 3: 미디어 검증, ffmpeg, VAD/normalize/denoise
- [x] Phase 4: 동의 기반 Voice Profile
- [x] Phase 5: 일반/장문 TTS
- [x] Phase 6: Speech VC
- [x] Phase 7: Source Separation + Singing VC + Mixing
- [x] Phase 8: ModelManager/GPU/OOM
- [x] Phase 9: SSE/ETA/Queue/History UX
- [x] Phase 10: logging/monitoring/cleanup/security/deploy
- [x] Phase 11: licensed real Korean TTS adapter and GPU acceptance

## Development Workflow

1. 중복 여부와 설계 문서를 확인합니다.
2. 상세한 한국어 GitHub Issue를 먼저 만듭니다.
3. `<type>/#<issue-number>-<topic>` branch를 만듭니다.
4. 구현, 테스트, 문서를 같은 작업에서 갱신합니다.
5. 논리적 단위별 한국어 commit 후 push합니다.
6. 최종 검증 후 `develop`에 merge하고 Issue를 닫습니다.

`develop`이나 `main`에서 기능을 직접 개발하지 않습니다. 이 저장소는 초기 상태라 Issue #1 완료 시 `develop`을 처음 생성합니다.

## Git Convention

- Branch: `feat/#10-tts-pipeline`, `fix/#18-worker-oom`, `docs/#21-api-document`
- Commit: `[feat] 모델 레지스트리와 Mock 어댑터 구현`
- 허용 type: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`

“수정”, “업데이트”, “코드 변경”처럼 의도가 드러나지 않는 제목은 사용하지 않습니다.

## CI/CD

GitHub Actions는 push/PR에서 backend Ruff·mypy·pytest, frontend ESLint·Vitest·TypeScript build, Docker build를 검증합니다. GPU inference는 일반 CI에서 실행하지 않고 Mock 계약 테스트와 별도 GPU runner 검증으로 분리합니다.

## Security

Voice Cloning은 명시적 권한이 있는 음성만 허용합니다. 동의 없이는 Profile을 생성하지 않으며 원본/파생 데이터의 추적 삭제, UUID storage key, MIME/컨테이너 검증, 제한된 media probe, 민감 로그 차단을 적용합니다. 구현 계약은 [Voice Profile 문서](docs/voice-profiles.md), [보안 문서](docs/security.md), [운영 Runbook](docs/operations.md)에 있습니다.

## Troubleshooting

- `API offline`: backend가 8000 포트에서 실행 중인지 `/api/health`를 확인하세요.
- DB 연결 실패: async driver가 URL과 일치하는지, compose의 `database` health를 확인하세요.
- Redis 연결 실패: `redis-cli ping`과 `REDIS_URL` host를 확인하세요. 컨테이너 내부 host는 `localhost`가 아니라 `redis`입니다.
- 모델 목록이 비어 있음: 개발 환경에서는 `USE_MOCK_INFERENCE=true`인지 확인하세요. 실제 TTS는 Worker와 API에 동일하게 `ENABLE_COSYVOICE3=true`를 적용하고 checkpoint/runtime mount를 제공해야 등록됩니다.
- CUDA를 찾지 못함: NVIDIA driver, Container Toolkit, compose GPU override와 `nvidia-smi`를 순서대로 확인하세요.
- migration 불일치: 임의로 테이블을 만들지 말고 `cd backend && alembic upgrade head`를 실행하세요.
