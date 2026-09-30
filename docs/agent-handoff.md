# 후속 에이전트 인수인계 문서

이 문서는 이전 대화 기록 없이 Voice Fairy Tale 개발을 이어가기 위한 단일 진입점이다. 먼저 이 문서를 끝까지 읽고, 링크된 설계 문서는 현재 작업에 필요한 것만 추가로 확인한다.

## 1. 현재 상태

- 저장소: <https://github.com/apg0001/USER-VOICE-FAIRYTALE>
- 기준 브랜치: `develop`
- Phase 7 직전 `develop` commit: `5cdcc38`; 정확한 최신 commit은 `git rev-parse HEAD`로 확인
- 완료된 Phase: 1–7
- 다음 제품 Phase: [#8 GPU Model Manager와 OOM 복구](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/8)
- 별도 모델 검증: [#11 실제 한국어 TTS 모델 평가와 Adapter 연결](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/11)
- 현재 inference는 모두 계약 검증용 Mock이다. 실제 사람의 음색을 생성한다고 주장하면 안 된다.
- Phase 7 로컬 기준 검증: Ruff/mypy 통과, pytest 35 passed·2 skipped, coverage 81%, frontend lint/build 통과
- 최근 `develop` CI는 `gh run list --branch develop --limit 3`으로 확인

작업 시작 직후 다음을 다시 확인한다. 이 문서의 숫자보다 Git과 GitHub 상태가 우선한다.

```powershell
git status --short
git branch --show-current
git pull --ff-only origin develop
gh issue list --state open
gh run list --branch develop --limit 3
```

`develop`이 dirty하면 사용자 변경을 임의로 버리지 말고 겹치는 파일을 먼저 확인한다.

## 2. 제품 목표와 현재 경계

제품은 사용자가 명시적으로 권한을 확인한 Voice Profile로 다음 작업을 비동기 실행하는 확장 가능한 Voice AI Platform이다.

```text
Text ───────────────→ TTS ─────────────────────────┐
Speech ─────────────→ Voice Conversion ────────────┼→ JobOutput → Download
Music → Separation → Singing VC → Mixing ─────────┘
                       ▲
Voice Sample → Validation/Preprocess → Voice Profile
```

FastAPI는 요청·소유권·metadata를 담당하고, 무거운 inference는 Celery Worker만 실행한다. PostgreSQL의 `jobs`가 영속 상태의 기준이고 Redis는 Queue다. 바이너리는 Object Storage adapter 뒤에 저장한다.

현재 구현된 실제 동작:

- 동의 기반 Voice Profile 등록·조회·삭제
- WAV/MP3/M4A/FLAC magic/MIME/확장자 검증
- ffprobe/ffmpeg 기반 speech mono 24 kHz / singing stereo 44.1 kHz PCM WAV 변환, trim/normalize/denoise 선택
- Job 멱등 생성, 상태 전이, 진행률, Queue 위치, 취소, 재시도
- 일반/장문 TTS chunking과 Mock WAV 생성
- 사용자 namespace 기반 Speech VC 입력 업로드
- Speech VC chunking, 길이·frame 수·sample rate 보존 계약
- 독립적인 Separation/Singing adapter와 10초 chunk 기반 Singing VC
- 원본 보컬 RMS 정렬, -1 dBFS limiter, 반주 재합성, stem hash manifest
- Job 결과 metadata와 소유자 전용 다운로드
- Studio의 Voice Profile 등록, TTS/Speech/Singing 입력·생성·polling/download

아직 구현되지 않은 실제 동작:

- 실제 TTS/VC/SVC/분리 모델과 GPU dependency
- ModelManager, VRAM admission, CUDA OOM 복구
- SSE, 작업 이력·플레이어·완전한 취소/재시도 UX
- 인증 공급자, 저장 암호화, 감사 로그, rate limit, watermark/abuse 대응
- 입력 원본·정제본의 보존기간 cleanup과 사용자 전체 삭제

## 3. 변경하면 안 되는 핵심 원칙

1. API 프로세스에서 CUDA 모델을 import하거나 inference하지 않는다.
2. Queue message에는 `job_id`만 넣고 민감 데이터나 대용량 payload를 넣지 않는다.
3. 구체 모델을 pipeline에서 직접 참조하지 않고 adapter/registry 계약 뒤에 둔다.
4. 음성 바이너리를 DB BLOB으로 저장하지 않는다.
5. 사용자 파일명을 storage path로 사용하지 않는다.
6. 실제 동의 버전과 권한 선언을 음성 처리 전에 검증한다.
7. 다른 사용자의 profile, input key, job, output 존재 여부를 노출하지 않는다.
8. 계산할 근거가 없는 ETA나 품질 수치를 만들어내지 않는다.
9. 실제 모델의 라이선스와 weights 라이선스를 별도로 확인한다.
10. 코드, 테스트, README/관련 문서를 같은 작업에서 갱신한다.

현재 `X-User-ID`는 개발용 신뢰 경계일 뿐 인증이 아니다. 공개 운영 전에 검증된 인증 토큰 subject로 교체해야 한다.

## 4. 기술 스택과 실행 환경

| 영역 | 현재 선택 |
|---|---|
| API/Worker | Python 3.11+, FastAPI, SQLAlchemy async, Celery |
| DB/Queue | PostgreSQL, Redis; 테스트는 SQLite/aiosqlite |
| Media | ffmpeg/ffprobe, Python `wave` 기반 Mock pipeline |
| Frontend | React, TypeScript, Vite, Nginx |
| Storage | `ObjectStorage` 계약과 `LocalObjectStorage` |
| 품질 | Ruff 0.16.9, mypy 1.20.2, pytest, ESLint, TypeScript |
| 배포 검증 | Docker Compose, GitHub Actions |

현재 Windows 개발 머신에는 ffmpeg와 Docker가 설치되지 않았다. 이 때문에 로컬 ffmpeg 통합 테스트 하나는 skip되며 Docker는 GitHub Actions에서 검증한다. Ubuntu CI에서는 ffmpeg 테스트와 backend/frontend/Docker build가 모두 실행된다.

GitHub Actions의 Node.js 20 deprecation 및 향후 `ubuntu-latest` 이미지 변경 경고는 알려져 있다. 기능 실패는 아니지만 Phase 10 이전에 action major version을 점검한다.

## 5. 코드 지도

```text
backend/app/
├─ api/
│  ├─ jobs/                 # Job 생성·조회·취소·재시도 schema/routes
│  ├─ voices/               # 동의, Voice Profile 등록·조회·삭제
│  ├─ files.py              # 입력 업로드와 결과 다운로드
│  └─ models.py             # capability별 모델 descriptor
├─ audio/
│  ├─ validation.py         # magic/MIME/확장자 검증
│  ├─ ffmpeg.py             # shell-free ffprobe/ffmpeg와 품질 분석
│  ├─ mixing.py             # RMS 정렬, 반주 mixing, peak limiter
│  └─ preprocessing/        # 전처리 orchestration
├─ db/models.py             # User, VoiceProfile, VoiceSample, Job, JobOutput
├─ models/
│  ├─ base.py               # 공통 VoiceModel/descriptor/capability
│  ├─ tts/                  # TTSModel, Mock, registry
│  ├─ voice_conversion/     # VoiceConversionModel, Mock, registry
│  ├─ separation/           # SeparationModel, Mock, registry
│  └─ singing/              # SingingVoiceModel, Mock, registry
├─ pipelines/
│  ├─ tts.py                # text chunk → synthesize → WAV 저장
│  ├─ voice_conversion.py   # PCM chunk → convert → 보존 검증 → WAV
│  └─ singing.py            # separation → SVC → mixing → manifest
├─ profiles/                # 모델별 Voice Profile builder
├─ queue/                   # JobQueue 계약과 Celery adapter
├─ services/
│  ├─ file_service.py       # 검증·전처리·원본/정제본 저장
│  ├─ job_service.py        # 소유권, 멱등성, 상태 전이, ETA
│  ├─ user_service.py       # 개발 identity → User 해석
│  └─ voice_service.py      # 동의와 profile 수명주기
├─ storage/                 # storage port와 local adapter
└─ workers/inference_worker.py
                              # 현재 mode dispatch와 durable transition
```

`inference_worker.py`가 Phase 5–7 mode 분기로 커졌다. Phase 8 ModelManager를 연결할 때 mode executor/dispatcher 분리를 검토하되 기존 상태 전이·결과 정리 규칙을 보존한다.

Frontend의 단일 주요 화면은 `frontend/src/App.tsx`다. Phase 9 전까지 과도한 상태관리 library를 추가하지 않는다.

## 6. 데이터와 소유권 계약

- `users.external_id`: 현재 `X-User-ID`와 연결
- `voice_profiles`: owner, consent version/time, `PROCESSING|READY|REJECTED|DELETION_PENDING`
- `voice_samples`: original/cleaned key, probe/quality metadata
- `jobs`: mode, status, profile/input, progress, timing, error, metrics
- `job_outputs`: storage key, content type, duration, result metadata

상태 흐름:

```text
QUEUED → PREPROCESSING → LOADING_MODEL → INFERENCE → POSTPROCESSING → COMPLETED
   └──────────────── 각 단계에서 FAILED 또는 CANCELLED ────────────────┘
```

모든 음성 Job에는 요청자 소유의 `READY` Voice Profile이 필요하다. Speech/Singing 입력 key는 `users/{internal_user_id}/inputs/` prefix를 만족해야 한다. 결과 다운로드는 `job_outputs → jobs → users` join으로 소유권을 검증한다.

주의: 입력 업로드는 원본과 정제본을 저장하지만 별도 upload table이 없다. 정제 key만 클라이언트에 반환되고 보존기간 정리는 아직 없다. Phase 10에서 추적 metadata와 cleanup을 완성해야 한다.

## 7. 현재 API

| Method | Path | 역할 |
|---|---|---|
| GET | `/api/health` | liveness/version |
| GET | `/api/models` | capability별 모델 목록 |
| GET | `/api/voices/consent` | 현재 동의문과 버전 |
| POST/GET | `/api/voices` | Voice Profile 등록/목록 |
| GET/DELETE | `/api/voices/{id}` | 소유자 조회/삭제 |
| POST | `/api/files/inputs` | 소유자 namespace 입력 검증·전처리 |
| GET | `/api/files/{output_id}` | 소유자 전용 결과 stream |
| POST/GET | `/api/jobs` | Job 생성/목록 |
| GET | `/api/jobs/{id}` | 상태, 진행률, output 조회 |
| POST | `/api/jobs/{id}/cancel` | cooperative cancel |
| POST | `/api/jobs/{id}/retry` | 실패·취소 Job 재시도 |

OpenAPI는 실행 후 `http://localhost:8000/api/docs`에서 확인한다.

## 8. 설정

전체 기본값은 `.env.example`과 `backend/app/core/config.py`가 기준이다. 주요 값:

- `DATABASE_URL`, `REDIS_URL`
- `STORAGE_PATH`, `MODEL_PATH`
- `FFMPEG_PATH`, `FFPROBE_PATH`
- `CUDA_DEVICE`
- `MAX_UPLOAD_SIZE`, `MAX_AUDIO_DURATION_SECONDS`
- `VOICE_CONSENT_VERSION`, `MIN_VOICE_PROFILE_SPEECH_SECONDS`
- `MODEL_CACHE_LIMIT`, `USE_MOCK_INFERENCE`, `TEMP_RETENTION_HOURS`

환경값을 새로 추가하면 두 파일과 README 환경 변수 표를 함께 갱신한다. 실제 `.env`나 secret은 commit하지 않는다.

## 9. 검증 명령

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
npm run build
```

변경 전 최종 확인:

```powershell
git diff --check
git status --short
```

Docker가 있는 환경:

```bash
docker compose build
docker compose up
```

로컬 성공만으로 병합하지 않는다. feature branch GitHub Actions의 backend, frontend, docker가 모두 성공해야 한다. `develop` 병합 후 생성된 CI도 다시 확인한다.

## 10. Git/GitHub 작업 절차

모든 큰 작업은 기존 한국어 Issue를 확인하고 다음 순서를 지킨다.

```powershell
git switch develop
git pull --ff-only origin develop
git switch -c "feat/#8-model-manager"
```

구현 후 논리 단위로 커밋한다.

```text
[feat] GPU 진단과 Model Manager 구현
[test] Model lifecycle과 OOM 복구 계약 검증
[docs] GPU 운영 경계와 장애 복구 문서화
```

그 다음:

```powershell
git push -u origin "feat/#8-model-manager"
gh run list --branch "feat/#8-model-manager" --limit 3
gh run watch RUN_ID --exit-status
git switch develop
git pull --ff-only origin develop
git merge --no-ff "feat/#8-model-manager" -m "[feat] GPU Model Manager를 develop에 병합"
git push origin develop
```

`develop` CI가 성공한 뒤에만 상세 검증 댓글과 함께 Issue를 닫는다. `main`에는 직접 병합하지 않는다.

## 11. 다음 작업: Phase 8 권장 구현 순서

Issue: [#8](https://github.com/apg0001/USER-VOICE-FAIRYTALE/issues/8)
Branch: `feat/#8-model-manager`

권장 순서:

1. Issue 본문과 `docs/architecture.md`의 GPU/Model lifecycle 계약을 다시 확인한다.
2. `(model key, version, device)` 단위 cache entry와 adapter factory를 관리하는 `ModelManager`를 만든다.
3. 동일 모델의 중복 load를 막는 per-entry lock과 Worker concurrency 1 전제를 명시한다.
4. CUDA/PyTorch가 없어도 import와 CPU Mock 테스트가 가능한 진단 port를 둔다.
5. lazy load 전에 VRAM admission을 수행하고, 부족하면 참조되지 않는 LRU entry를 unload한다.
6. Job 실행 동안 lease/reference를 유지해 사용 중 모델이 eviction되지 않게 한다.
7. CUDA OOM은 현재 Job만 실패시키고 adapter 참조 해제, GC, CUDA cache 정리 후 Worker health를 재검증한다.
8. TTS/VC/Singing의 직접 `load/unload`를 Manager lease로 교체한다. Singing은 separator와 SVC 두 lease가 필요하다.
9. model load time, cache hit/miss, eviction, device/VRAM metadata를 민감정보 없이 Job metric/log에 남긴다.
10. CPU fake device/model로 cache, lock, eviction, load 실패, OOM 격리 test를 작성한다.
11. 실제 GPU smoke test는 일반 CI와 분리하고 실행 조건과 증거를 문서화한다.

Phase 8 완료 조건:

- 같은 모델의 동시 요청이 중복 load되지 않는다.
- 사용 중 모델은 eviction되지 않고 LRU 제한이 지켜진다.
- OOM 후 다음 Mock Job을 처리할 수 있으며 Worker 전체가 종료되지 않는다.
- API image에 PyTorch/CUDA dependency가 추가되지 않는다.
- GPU가 없는 CI에서 전체 계약을 재현할 수 있다.

## 12. 이후 작업 순서

### Phase 8 — Issue #8

- `ModelManager` lazy load/unload, model key/version/device cache
- per-model lifecycle lock, Worker당 명시적 GPU 소유권
- CUDA/GPU/VRAM/PyTorch 진단
- VRAM admission과 LRU unload
- CUDA OOM → 현재 Job 실패 → 참조 제거/GC/cache 정리 → Worker health 확인
- GPU 없는 CI Mock test와 실제 GPU runner 절차 분리

### Phase 9 — Issue #9

- SSE endpoint와 reconnect cursor/heartbeat
- polling fallback 유지
- 작업 이력, 취소, 재시도, Queue 위치, ETA
- audio player와 인증된 blob download
- 빈 상태·오류·재연결·접근성 frontend test
- 현재 `App.tsx`의 1초 polling loop와 단일 컴포넌트 상태를 정리

### Phase 10 — Issue #10

- request/job/model/stage 구조화 로그와 민감정보 redaction
- metrics/trace, health/readiness, queue/worker/storage 지표
- temp/intermediate/input/output 보존기간 cleanup
- Voice Profile 및 사용자 전체 데이터 삭제
- storage failure retry와 orphan reconciliation
- 인증/인가, rate limit, 감사 로그, 암호화, abuse report/watermark 확장점
- 운영 runbook, backup/restore, deploy/rollback
- GitHub Actions Node/runtime major version 갱신

### 실제 모델 acceptance — Issue #11

Phase 8의 ModelManager/Worker image 경계가 준비된 뒤 진행하는 편이 안전하다.

- 후보의 코드와 weights 라이선스를 각각 출처와 함께 기록
- 한국어 고정 corpus로 발음·화자 유사도·긴 문장 안정성 평가
- GPU별 peak VRAM, load time, RTF 기록
- Mock과 실제 adapter에 같은 contract suite 적용
- 무거운 dependency는 Worker image에만 추가

## 13. 알려진 기술 부채와 함정

- Mock TTS는 tone WAV, Mock VC/SVC는 gain 변환, Mock separation은 sample 비율 분할이다. 품질 검증용 모델이 아니다.
- `inference_worker.py`의 mode dispatch가 커지고 있어 Phase 8 Manager 연결 시 executor 분리를 검토한다.
- TTS/VC/Singing checkpoint는 진행 metadata를 영속화하지만 chunk 오디오 재개 manifest까지 구현하지 않았다.
- 입력 업로드의 원본·정제본 보존기간과 DB 추적은 Phase 10 과제다.
- Voice Profile 삭제 후 과거 Job의 `voice_profile_id`는 `SET NULL`이므로 결과 provenance 보존 정책을 재검토해야 한다.
- File upload copy logic이 voice/files route에 일부 중복된다. 공통 upload staging service 후보지만 작은 함수 하나를 위해 과도하게 추상화하지 않는다.
- LocalObjectStorage는 개발 adapter다. S3/MinIO 도입 시 streaming put, multipart, retry, checksum, delete reconciliation을 구현한다.
- 취소는 단계 경계에서 협력적으로 확인한다. 실제 긴 GPU kernel은 즉시 중단되지 않을 수 있다.
- frontend는 `local-developer` identity를 하드코딩한다. 인증 도입 전 공개 배포 금지다.
- profile builder의 실제 embedding/object 생성 시 파생 key를 삭제 경로에 반드시 등록한다.
- 모델 후보 저장소가 archived이거나 GPL일 수 있다. process 분리는 라이선스 의무를 없애지 않는다.

## 14. 관련 문서 읽기 순서

1. 이 문서
2. [README](../README.md)
3. [아키텍처](architecture.md)
4. 현재 Phase 관련 문서
   - [Audio Pipeline](audio-pipeline.md)
   - [Job System](job-system.md)
   - [Voice Profile](voice-profiles.md)
   - [TTS Pipeline](tts-pipeline.md)
   - [Speech Voice Conversion](speech-voice-conversion.md)
   - [Singing Voice Conversion](singing-voice-conversion.md)
5. [보안 원칙](security.md)

문서와 코드가 다르면 코드를 확인하고 같은 작업에서 문서를 바로 고친다.

## 15. 매 작업 완료 체크리스트

- [ ] Issue 목적과 완료 조건을 충족했다.
- [ ] 소유권·동의·민감정보 경계를 검토했다.
- [ ] API process에 무거운 inference dependency를 넣지 않았다.
- [ ] 실패·취소 시 해당 Job의 객체만 정리된다.
- [ ] unit/API/integration test를 추가했다.
- [ ] Ruff, mypy, pytest, frontend lint/build가 통과했다.
- [ ] README와 관련 문서가 실제 코드와 일치한다.
- [ ] 논리 단위의 한국어 commit을 만들었다.
- [ ] feature branch CI의 backend/frontend/docker가 모두 성공했다.
- [ ] `develop`에 `--no-ff` 병합하고 push했다.
- [ ] `develop` CI 성공을 확인했다.
- [ ] 검증 링크와 한계를 Issue 댓글에 기록하고 닫았다.
- [ ] 작업 트리가 clean인지 확인했다.

완료를 서두르기 위해 실제 모델 품질, 보안 또는 테스트 상태를 과장하지 않는다. 구현되지 않은 기능은 문서와 UI에서 명확히 비활성 또는 Mock으로 표시한다.
