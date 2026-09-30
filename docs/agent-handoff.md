# 후속 에이전트 인수인계 문서

이 문서는 이전 대화 기록 없이 Voice Fairy Tale 개발을 이어가기 위한 단일 진입점이다. 먼저 끝까지 읽고, 현재 Git/GitHub 상태를 확인한 뒤 작업한다. 문서보다 코드와 원격 상태가 우선이지만 불일치가 있으면 같은 작업에서 문서를 고친다.

## 1. 저장소와 현재 상태

- 저장소: <https://github.com/apg0001/USER-VOICE-FAIRYTALE>
- 기준 브랜치: `develop`
- 완료 Phase: 1–10
- 다음 이슈: [#11 실제 한국어 TTS 모델 품질·라이선스 평가와 Adapter 연결](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/11)
- 현재 inference adapter는 모두 계약 검증용 Mock이다. 실제 사람 음색 품질을 제공한다고 주장하면 안 된다.
- Phase 10 로컬 검증 기준: backend Ruff/mypy 통과, pytest 54 passed·2 skipped, coverage 82%; frontend 6 passed·lint/build 통과; Alembic `base → 0003 → base` 왕복 통과
- Windows 개발 머신에는 ffmpeg와 Docker가 없어 ffmpeg 통합 2개가 skip될 수 있다. Ubuntu GitHub Actions에서 ffmpeg와 Docker build를 검증한다.

작업 시작 시 반드시 다음을 실행한다.

```powershell
git status --short
git branch --show-current
git log -5 --oneline
gh issue list --state open
gh run list --branch develop --limit 3
```

`develop`이 clean이고 최신인지 확인한 뒤 새 이슈 branch를 만든다. dirty하면 사용자 변경을 버리지 말고 겹치는 파일을 먼저 확인한다.

## 2. 제품 경계

사용자가 권리를 확인하고 동의한 Voice Profile로 TTS, Speech Voice Conversion, Singing Voice Conversion을 비동기 실행하는 플랫폼이다.

```text
Browser → React/Nginx → FastAPI → PostgreSQL
                         │  ├──→ Object Storage
                         │  └──→ Redis → Celery Worker → GPU/Models
                         └─────→ SSE/polling status

Celery Beat → retention cleanup → DB metadata + Object Storage
```

FastAPI는 인증 경계, schema, owner 검증, Job/파일 metadata만 처리한다. 무거운 inference와 CUDA import는 Worker에서만 한다. PostgreSQL `jobs`가 영속 상태의 기준이고 Redis는 전달 계층이다. 음성 binary는 DB BLOB가 아니라 `ObjectStorage` 뒤에 둔다.

현재 구현된 동작:

- 오디오 magic/MIME/확장자·크기·길이 검증과 ffmpeg 전처리
- 동의 버전·권리 선언 기반 Voice Profile 등록, owner 조회, pending 삭제
- Job 멱등 생성, 상태 전이, 진행률, Queue 위치, ETA, 취소, retry
- 일반/장문 Mock TTS, Speech VC, Separation/Singing VC, loudness/mixing
- ModelManager lease, lazy load, VRAM admission, idle LRU, CUDA OOM 격리
- owner 전용 SSE, Last-Event-ID, heartbeat, reconnect와 polling fallback
- Studio 이력, cancel/retry, 오류 표시, 결과 재생·다운로드
- 입력 원본/정제본 owner·만료 추적, 출력 만료와 synthetic provenance
- 15분 cleanup, storage 실패 retry, active input skip, orphan 유예 정리
- owner 범위 사용자 전체 데이터 삭제와 삭제 중 계정 차단
- JSON 로그 redaction, request/trace correlation, Prometheus 지표
- liveness/readiness 분리, production trusted-proxy 강제, process rate limit
- 배포/rollback/backup/restore/incident runbook과 배포 검증 script

아직 구현되지 않은 동작:

- 실제 TTS/VC/SVC/분리 model adapter와 GPU acceptance
- 실제 IdP 또는 인증 gateway 배포 구성
- S3/MinIO storage adapter, at-rest encryption, multipart/checksum
- 실제 비가청 watermark provider와 abuse moderation sink/API
- 분산 rate limit와 중앙 trace backend

이 미구현 항목을 문서나 UI에서 구현된 것으로 표현하지 않는다.

## 3. 변경하면 안 되는 원칙

1. API process에서 CUDA 모델을 import하거나 inference하지 않는다.
2. Queue payload에는 `job_id`만 넣고 text, audio, token, config 전문을 넣지 않는다.
3. 구체 모델은 adapter/registry/manager 계약 뒤에서만 선택한다.
4. 사용자 파일명은 storage path에 사용하지 않고 UUID key를 발급한다.
5. 모든 profile/input/job/output API는 DB owner 조건으로 존재와 권한을 동시에 검사한다.
6. Voice Profile 처리 전에 현재 동의 버전과 사용 권한 선언을 검증한다.
7. 계산 근거가 없는 ETA·품질·GPU 수치를 만들지 않는다.
8. storage 삭제 성공 전에 추적 metadata를 삭제하지 않는다.
9. 로그에 raw subject, input text, audio bytes, token, storage key, 절대경로를 남기지 않는다.
10. 실제 model code와 weights의 라이선스·출처·배포 의무를 각각 확인한다.
11. 코드, 테스트, README, 관련 문서를 같은 branch에서 함께 갱신한다.
12. feature CI와 develop merge CI가 모두 성공한 뒤 이슈를 닫는다.

## 4. 인증과 운영 보안

`AUTH_MODE=development_header`는 `X-User-ID`를 사용하는 로컬/CI 전용 모드다. `APP_ENV=production`에서 이 모드면 app 생성이 실패한다.

운영 모드는 `AUTH_MODE=trusted_proxy`이며 32자 이상의 `TRUSTED_PROXY_SECRET`, gateway가 검증한 `X-Authenticated-User`, private backend network가 모두 필요하다. Gateway는 외부가 보낸 두 header를 제거하고 새로 주입해야 한다. 이것은 IdP 자체가 아니라 IdP 검증을 끝낸 gateway와 backend 사이의 계약이다.

Rate limiter는 process-local fixed window다. 공개 배포에는 ingress/Redis 기반 분산 quota를 추가한다. `/api/metrics`는 인증 endpoint가 아니므로 monitoring network만 접근하게 ingress에서 차단한다.

## 5. 핵심 코드 지도

```text
backend/app/
├─ api/
│  ├─ dependencies.py       # development/trusted-proxy actor 경계
│  ├─ health.py             # /health, /ready, /metrics
│  ├─ files.py              # input ingest 추적, owner output stream
│  ├─ users.py              # DELETE /users/me
│  ├─ jobs/                 # create/list/detail/cancel/retry/SSE
│  └─ voices/               # consent/profile lifecycle
├─ core/
│  ├─ config.py             # 환경변수의 typed source
│  ├─ logging.py            # JSON + recursive sensitive redaction
│  ├─ metrics.py            # bounded label Prometheus registry
│  ├─ rate_limit.py         # process-local 방어
│  └─ gpu.py                # PyTorch lazy CUDA 진단
├─ db/models.py             # User/InputArtifact/Profile/Sample/Job/Output
├─ models/
│  ├─ manager.py            # lease/lock/LRU/VRAM/OOM
│  ├─ tts/                  # 다음 Phase의 실제 adapter 위치
│  ├─ voice_conversion/
│  ├─ separation/
│  └─ singing/
├─ pipelines/               # TTS/Speech/Singing orchestration
├─ profiles/                # model별 Voice Profile builder
├─ safety/base.py           # provenance, watermark/abuse port
├─ services/
│  ├─ cleanup_service.py    # retention, retry, full delete, orphan reconcile
│  ├─ file_service.py       # original/cleaned ingest
│  ├─ job_service.py        # owner/state/idempotency/ETA
│  ├─ user_service.py       # external subject → User
│  └─ voice_service.py      # consent/profile deletion
├─ storage/                 # ObjectStorage + LocalObjectStorage
└─ workers/
   ├─ celery_app.py         # inference + cleanup task, beat schedule
   ├─ cleanup_worker.py     # scheduled/manual cleanup
   ├─ inference_worker.py   # mode dispatch, output/provenance, durable state
   └─ model_runtime.py      # process-scoped ModelManager
```

주요 운영 파일:

- `backend/alembic/versions/0003_data_retention.py`: input tracking, user deletion timestamp, output expiry
- `docs/operations.md`: 배포·관측·보존·백업·복구·장애 대응의 기준
- `docs/security.md`: 위협 모델과 잔여 통제
- `scripts/verify_deployment.py`: health/readiness/metrics smoke test
- `.github/workflows/ci.yml`: backend/frontend/Docker CI

## 6. 데이터·수명주기 계약

| Table | 책임 |
|---|---|
| `users` | 외부 subject와 내부 owner, 삭제 요청/비활성 상태 |
| `input_artifacts` | 업로드 원본·정제 key, owner, kind, 만료, 삭제 retry |
| `voice_profiles` | 동의 버전/시각, profile 상태와 model profile metadata |
| `voice_samples` | profile 원본·정제 key와 품질 metadata |
| `jobs` | mode/status/progress/input/model/error/metrics의 기준 |
| `job_outputs` | 결과 key, duration, provenance, 만료 |

보존 기본값:

- 입력 원본/정제본: 24시간
- 출력: 168시간
- Voice Profile sample: profile 또는 사용자 삭제까지
- pipeline temp/intermediate: 종료 즉시 삭제
- orphan: DB 미추적 + 알려진 namespace + 2시간 유예 후 삭제

Cleanup 순서는 expired input → expired output → pending profile → pending user → orphan이다. 활성 Job이 참조하는 입력은 만료되어도 건너뛴다. 기존 output처럼 `expires_at`이 null이면 `created_at + OUTPUT_RETENTION_HOURS`를 적용한다.

사용자 전체 삭제는 active Job을 먼저 취소하고 owner query로 sample/input/output exact key를 모은다. 하나라도 storage delete가 실패하면 user metadata를 유지하고 `is_active=false`, `deletion_requested_at!=null`로 두어 API 접근을 410으로 차단한다. Scheduler가 재시도한다.

## 7. Job·모델 계약

상태 흐름:

```text
QUEUED → PREPROCESSING → LOADING_MODEL → INFERENCE → POSTPROCESSING → COMPLETED
   └──────────────── 각 단계에서 FAILED 또는 CANCELLED ────────────────┘
```

- 상태별 progress 범위를 벗어나거나 감소하면 거부한다.
- `Idempotency-Key`는 동일 owner 내 중복 dispatch를 막는다.
- retry는 FAILED/CANCELLED만 가능하며 profile/input이 삭제·만료되었으면 422다.
- TTS/VC/Singing 결과에는 `OutputProvenance(model_key, model_version)`가 들어간다.
- 실제 watermark는 `WatermarkProvider`를 구현해 publish 전에 적용해야 한다.
- `AbuseReportSink`는 moderation system 연결 port일 뿐 기본 sink/API는 없다.

ModelManager cache key는 `(role, model key, version, device)`다. lease 중인 model은 eviction하지 않는다. CUDA OOM은 해당 Job만 실패시키고 idle cache/GC/CUDA cache를 정리한 뒤 다음 Job이 실행 가능해야 한다.

## 8. 현재 API

| Method | Path | 역할 |
|---|---|---|
| GET | `/api/health` | process liveness/version |
| GET | `/api/ready` | DB/storage/선택적 Redis readiness |
| GET | `/api/metrics` | Prometheus 요청·Job·삭제 지표 |
| GET | `/api/models` | capability별 adapter descriptor |
| GET | `/api/voices/consent` | 현재 동의문/버전 |
| POST/GET | `/api/voices` | profile 등록/목록 |
| GET/DELETE | `/api/voices/{id}` | owner 조회/pending 삭제 |
| POST | `/api/files/inputs` | 입력 검증·전처리·추적 |
| GET | `/api/files/{output_id}` | owner 결과 stream |
| POST/GET | `/api/jobs` | 작업 생성/목록 |
| GET | `/api/jobs/{id}` | owner 상태/결과 |
| GET | `/api/jobs/{id}/events` | cursor/heartbeat SSE |
| POST | `/api/jobs/{id}/cancel` | cooperative cancel |
| POST | `/api/jobs/{id}/retry` | terminal retry |
| DELETE | `/api/users/me` | 전체 owner 데이터 추적 삭제 |

## 9. 환경 설정

기준은 `.env.example`과 `backend/app/core/config.py`다.

필수 영역:

- Runtime: `APP_ENV`, `LOG_LEVEL`, `FRONTEND_ORIGINS`
- Auth: `AUTH_MODE`, `TRUSTED_PROXY_SECRET`, `RATE_LIMIT_REQUESTS_PER_MINUTE`
- Infra: `DATABASE_URL`, `REDIS_URL`, `STORAGE_PATH`, `MODEL_PATH`
- Media: `FFMPEG_PATH`, `FFPROBE_PATH`, upload/duration 제한
- GPU: `CUDA_DEVICE`, `MODEL_CACHE_LIMIT`, `GPU_VRAM_RESERVE_MB`
- Streaming: `SSE_POLL_INTERVAL_SECONDS`, `SSE_HEARTBEAT_SECONDS`
- Retention: `INPUT_RETENTION_HOURS`, `OUTPUT_RETENTION_HOURS`, `CLEANUP_BATCH_SIZE`, `ORPHAN_GRACE_HOURS`
- Model: `USE_MOCK_INFERENCE`, consent/minimum speech 설정

새 설정을 추가하면 config, `.env.example`, compose, README, 운영 문서를 함께 갱신한다. 실제 secret이나 `.env`는 commit하지 않는다.

## 10. 검증 명령

Backend:

```powershell
cd backend
python -m ruff check app tests
python -m mypy app
python -m pytest --cov=app --cov-report=term-missing
```

Frontend:

```powershell
cd frontend
npm ci
npm run lint
npm test
npm run build
```

Migration은 임시 SQLite 또는 PostgreSQL test DB에서 `alembic upgrade head`, `alembic current`, 필요 시 `alembic downgrade base`를 검증한다. 실제 production DB를 downgrade 테스트에 사용하지 않는다.

배포 후:

```powershell
python scripts/verify_deployment.py http://localhost:8000
```

최종 확인:

```powershell
git diff --check
git status --short
```

로컬 성공만으로 병합하지 않는다. feature branch GitHub Actions의 backend/frontend/docker를 확인하고 develop merge 후 CI도 다시 확인한다.

## 11. Git/GitHub 규칙

1. 상세 한국어 Issue를 확인하거나 먼저 생성한다.
2. `<type>/#<number>-<topic>` branch에서 작업한다.
3. 논리 단위별 한국어 commit을 만든다.
4. feature branch를 push하고 CI 성공을 확인한다.
5. `develop`에 `--no-ff` merge하고 push한다.
6. develop CI 성공 뒤 검증 링크와 한계를 Issue에 기록하고 닫는다.
7. `main`에는 직접 merge하지 않는다.

예시:

```powershell
git switch develop
git pull --ff-only origin develop
git switch -c "feat/#11-real-tts-adapter"
```

## 12. 다음 작업: Issue #11

Issue #11은 외부 정보와 실제 GPU가 필요한 acceptance 작업이다. 후보를 이름만 보고 채택하지 않는다.

권장 순서:

1. `docs/tts-pipeline.md`, `backend/app/models/tts/base.py`, registry, ModelManager와 Worker image 경계를 읽는다.
2. OpenVoice V2 + 한국어 base TTS, CosyVoice 등 후보의 공식 repository/model card/paper만 조사한다.
3. 코드 라이선스와 checkpoint/weights 라이선스를 별도 표로 기록한다. 상업 이용, 재배포, attribution, voice-cloning restriction이 불명확하면 채택하지 않는다.
4. 한국어 고정 corpus와 평가 protocol을 먼저 확정한다. 숫자 기준 없이 청취 인상만으로 결정하지 않는다.
5. 동일 GPU에서 load time, peak VRAM, real-time factor, 실패율을 측정한다. GPU 정보와 precision/batch/chunk 조건을 함께 기록한다.
6. 발음/숫자/영문 혼용/장문 경계와 화자 유사도 평가를 분리한다. 동의된 음성만 쓴다.
7. 채택 adapter는 `TTSModel` 계약 뒤에 두고 무거운 dependency는 별도 Worker image/optional dependency에만 넣는다.
8. Mock과 실제 adapter에 같은 contract suite를 적용한다. API image에서 실제 model package가 import되지 않는 테스트를 유지한다.
9. model key/version/source/checkpoint hash/license를 provenance와 배포 문서에 남긴다.
10. 실제 GPU runner가 없으면 코드·Mock으로 통과했다고 acceptance 완료로 표시하지 말고, 무엇이 미검증인지 Issue에 명시한다.

외부 모델 조사에는 최신 정보가 필요하므로 공식 출처를 다시 확인한다. 다운로드 전에 license와 파일 크기, 저장 위치를 검토한다. 대형 weights를 Git에 commit하지 않는다.

## 13. 알려진 기술 부채와 함정

- `LocalObjectStorage`는 단일 host 개발 adapter다. S3/MinIO의 streaming multipart, checksum, retry, encryption은 없다.
- Prometheus HTTP counter와 rate limiter는 process-local이다. 다중 replica는 scrape 합산과 ingress 분산 limit가 필요하다.
- Readiness의 Redis ping은 broker reachability만 보여주며 Worker heartbeat 자체를 보장하지 않는다.
- `inference_worker.py`가 mode dispatch와 lifecycle을 함께 가진다. 실제 adapter가 늘면 executor 분리를 검토하되 상태 전이/cleanup 규칙을 보존한다.
- TTS/VC/Singing Mock은 품질 모델이 아니다. tone/gain/sample 분리 결과를 실제 음질 근거로 쓰지 않는다.
- provenance metadata는 watermark가 아니다. provider가 연결되기 전 UI에서 watermark 적용으로 표시하지 않는다.
- Abuse report는 port만 있고 접수 API/moderation queue가 없다.
- Celery Beat는 replica를 하나만 실행한다. 여러 scheduler가 떠도 cleanup은 멱등이어야 하지만 불필요한 load가 생긴다.
- 사용자 삭제와 실행 중 GPU kernel은 즉시 중단되지 않을 수 있다. Worker checkpoint가 cancel을 확인하고 생성 output을 삭제한다.
- 기존 output의 null expiry는 cleanup 시 created_at 기반으로 처리한다.
- 실제 인증 gateway는 repository에 포함되지 않는다. trusted proxy 설정만으로 public auth가 완성되는 것은 아니다.

## 14. 문서 읽기 순서

1. 이 문서
2. [README](../README.md)
3. 현재 작업 문서
   - 운영: [operations.md](operations.md), [security.md](security.md)
   - 실제 TTS: [tts-pipeline.md](tts-pipeline.md), [model-manager.md](model-manager.md)
4. [architecture.md](architecture.md)
5. 해당 코드와 테스트

문서와 코드가 다르면 코드를 확인하고 같은 branch에서 둘을 일치시킨다.

## 15. 완료 체크리스트

- [ ] Issue 목적과 완료 조건을 충족했다.
- [ ] owner/동의/민감정보/삭제 경계를 테스트했다.
- [ ] API process에 무거운 inference dependency를 넣지 않았다.
- [ ] 실패·취소·retry에서 해당 Job/object만 정리된다.
- [ ] migration upgrade와 backward-compatible application rollback을 검토했다.
- [ ] Ruff, mypy, pytest, frontend lint/test/build가 통과했다.
- [ ] README, 인수인계, 관련 설계·운영 문서를 갱신했다.
- [ ] 논리 단위별 한국어 commit을 만들었다.
- [ ] feature CI backend/frontend/docker가 모두 성공했다.
- [ ] develop에 `--no-ff` merge하고 develop CI를 확인했다.
- [ ] 검증 URL, commit, 알려진 한계를 Issue에 기록하고 닫았다.
- [ ] 최종 worktree가 clean이다.
