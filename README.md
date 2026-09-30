# Voice Fairy Tale

사용자가 동의하여 등록한 음색으로 텍스트, 말, 노래를 변환하는 확장 가능한 Voice AI Platform입니다. API 서버와 GPU 추론 Worker를 분리하고, 장시간 작업을 Queue 기반 Job으로 관리하는 것을 핵심 원칙으로 삼습니다.

> 현재 범위: **Phase 2 Job System**. 기반 구조와 함께 Job 생성·조회·취소·재시도, 상태 전이, Queue 위치, 단계 기반 진행률과 Mock Worker 실행이 구현되어 있습니다. 음성 업로드와 실제 모델 추론은 후속 이슈가 완료될 때까지 의도적으로 비활성화되어 있습니다.

## 주요 기능

- Text → User Voice: 일반 문장과 장문 TTS
- Speech → User Voice: 내용·타이밍·운율을 가능한 범위에서 보존하는 음색 변환
- Singing → User Voice: 보컬 분리, 전용 SVC, 기존 반주와 재합성
- 음성 품질 검증, 선택적 noise reduction, Voice Profile 관리
- 비동기 Job, 진행률, ETA, Queue 위치, 취소·재시도
- 교체 가능한 모델 adapter와 GPU lifecycle 관리

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
│  │  ├─ core/             # 환경 설정과 JSON logging
│  │  ├─ db/               # SQLAlchemy model/session
│  │  ├─ models/           # VoiceModel, Mock adapter, registry
│  │  ├─ queue/            # Celery를 감싸는 JobQueue 계약
│  │  ├─ services/         # Job 상태 전이와 application rule
│  │  ├─ storage/          # ObjectStorage와 안전한 local adapter
│  │  └─ workers/          # Celery app와 inference task 경계
│  └─ tests/{api,unit}/     # GPU가 필요 없는 테스트
├─ frontend/
│  ├─ src/                 # React Voice Studio
│  ├─ nginx.conf           # SPA와 /api reverse proxy
│  └─ Dockerfile
├─ docs/
│  ├─ architecture.md      # 12개 초기 설계 산출물
│  └─ security.md          # 동의·업로드·삭제 원칙
├─ docker-compose.yml      # CPU/Mock 개발 stack
├─ docker-compose.gpu.yml  # NVIDIA device override
└─ .github/workflows/ci.yml
```

## Directory / File Description

- `backend/app/main.py`: application factory, CORS, request ID, lifecycle
- `backend/app/core/config.py`: 환경 변수의 단일 typed source
- `backend/app/db/models.py`: users, profiles, samples, jobs, outputs, models
- `backend/app/models/base.py`: 모든 AI adapter가 지켜야 하는 lifecycle 계약
- `backend/app/models/registry.py`: 모델 key/capability별 동적 선택
- `backend/app/services/job_service.py`: 멱등 생성, 상태 전이, 취소·재시도, ETA
- `backend/app/queue/`: API 테스트와 Celery를 분리하는 Queue port/adapter
- `backend/app/storage/local.py`: UUID key와 경로 순회 방어를 갖춘 개발 저장소
- `backend/app/workers/inference_worker.py`: API와 inference 프로세스의 경계
- `frontend/src/App.tsx`: 현재 Phase를 정직하게 표시하는 Studio UI

파일이나 책임이 바뀌면 이 목록과 `docs/architecture.md`를 같은 commit에서 갱신합니다.

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
| GET | `/api/models` | 구현 | capability별 등록 모델 |
| POST/GET | `/api/voices` | Phase 4 | Voice Profile 등록·조회 |
| POST/GET | `/api/jobs` | 구현 | 멱등 작업 생성·목록 |
| GET | `/api/jobs/{id}` | 구현 | 소유자 범위 상태·진행률 조회 |
| POST | `/api/jobs/{id}/cancel` | 구현 | cooperative cancel |
| POST | `/api/jobs/{id}/retry` | 구현 | 실패·취소 작업 재시도 |
| GET | `/api/files/{id}` | Phase 3/5 | 권한 검사 후 결과 stream |

아직 구현되지 않은 endpoint를 빈 성공 응답으로 제공하지 않습니다.

## AI Worker

Celery Worker는 API와 별도 프로세스입니다. `voice.run_inference`는 DB에서 Job을 읽고 각 단계 상태를 commit하며 Mock 모델 lifecycle을 끝까지 실행합니다. 실제 오디오를 생성하지 않으며 대용량 PyTorch/CUDA 의존성은 향후 별도 Worker image에만 설치합니다.

## Queue

Redis는 broker이고 PostgreSQL의 `jobs`가 영속 상태의 기준입니다. 기본 Worker 설정은 late ack, prefetch 1, concurrency 1입니다. 상태는 `QUEUED → PREPROCESSING → LOADING_MODEL → INFERENCE → POSTPROCESSING → COMPLETED`이며 어느 단계에서든 `FAILED` 또는 협력적 `CANCELLED`로 종료할 수 있습니다.

생성 요청은 `Idempotency-Key`로 중복 dispatch를 차단합니다. 진행률은 상태별 허용 범위 안에서 단조 증가해야 하며 ETA는 동일 model/mode/GPU 실행 이력이 3개 이상일 때만 계산합니다. 상세 계약은 [Job System 문서](docs/job-system.md)에 있습니다.

## GPU / CUDA

기본 compose는 GPU 없이 Mock으로 실행됩니다. GPU가 있는 Linux host에서는 NVIDIA Container Toolkit 설치 후 다음 override를 사용합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

실제 모델 adapter가 아직 없으므로 `USE_MOCK_INFERENCE=false`는 현재 모델 목록을 비웁니다. Phase 8에서 시작 시 CUDA/GPU/VRAM/version 진단, admission control, lazy loading/LRU unload, OOM 격리와 cache 정리를 구현합니다.

## Docker

서비스는 `frontend`, `backend`, `worker`, `redis`, `database`입니다. 미디어와 모델은 named volume에 저장하고 DB와 Redis는 health check 뒤에 의존 서비스를 시작합니다.

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
| `MODEL_CACHE_LIMIT` | 1 | 동시에 유지할 모델 수 |
| `USE_MOCK_INFERENCE` | `true` | 개발/CI model registry |
| `TEMP_RETENTION_HOURS` | 24 | 디버그 임시 파일 최대 보존 |

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
npm run build
```

## API

예시:

```bash
curl http://localhost:8000/api/health
curl "http://localhost:8000/api/models?capability=general_tts"
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
- [ ] Phase 3: 미디어 검증, ffmpeg, VAD/normalize/denoise
- [ ] Phase 4: 동의 기반 Voice Profile
- [ ] Phase 5: 일반/장문 TTS
- [ ] Phase 6: Speech VC
- [ ] Phase 7: Source Separation + Singing VC + Mixing
- [ ] Phase 8: ModelManager/GPU/OOM
- [ ] Phase 9: SSE/ETA/Queue/History UX
- [ ] Phase 10: logging/monitoring/cleanup/security/deploy

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

GitHub Actions는 push/PR에서 backend Ruff·mypy·pytest, frontend ESLint·TypeScript build, Docker build를 검증합니다. GPU inference는 일반 CI에서 실행하지 않고 Mock 계약 테스트와 별도 GPU runner 검증으로 분리합니다.

## Security

Voice Cloning은 명시적 권한이 있는 음성만 허용합니다. 동의 없이는 Profile을 생성하지 않으며 원본/파생 데이터의 추적 삭제, UUID storage key, MIME/컨테이너 검증, 제한된 media probe, 민감 로그 차단을 적용합니다. 상세 원칙과 운영 전 필수 통제는 [보안 문서](docs/security.md)에 있습니다.

## Troubleshooting

- `API offline`: backend가 8000 포트에서 실행 중인지 `/api/health`를 확인하세요.
- DB 연결 실패: async driver가 URL과 일치하는지, compose의 `database` health를 확인하세요.
- Redis 연결 실패: `redis-cli ping`과 `REDIS_URL` host를 확인하세요. 컨테이너 내부 host는 `localhost`가 아니라 `redis`입니다.
- 모델 목록이 비어 있음: Phase 1에서는 `USE_MOCK_INFERENCE=true`여야 Mock이 등록됩니다.
- CUDA를 찾지 못함: NVIDIA driver, Container Toolkit, compose GPU override와 `nvidia-smi`를 순서대로 확인하세요.
- migration 불일치: 임의로 테이블을 만들지 말고 `cd backend && alembic upgrade head`를 실행하세요.
