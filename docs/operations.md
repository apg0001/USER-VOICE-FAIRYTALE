# 운영 Runbook

이 문서는 Voice Fairy Tale의 배포, 관측, 데이터 보존·삭제, 백업·복구와 장애 대응 절차의 기준이다. 명령은 저장소 루트에서 실행하며 실제 secret은 명령 기록이나 이슈에 남기지 않는다.

## 1. 운영 경계

운영 topology는 `frontend → backend → PostgreSQL/Redis/storage`, `worker → GPU`, `scheduler → cleanup task`다. API의 `/api/health`는 process liveness만, `/api/ready`는 DB·storage와 설정에 따라 Redis 의존성까지 확인한다. Docker healthcheck는 readiness를 사용한다.

공개 배포의 필수 조건:

- `APP_ENV=production`, `AUTH_MODE=trusted_proxy`, 충분히 긴 `TRUSTED_PROXY_SECRET`을 secret manager에서 주입한다.
- 인증 gateway만 backend에 접근할 수 있게 network policy를 적용하고, gateway가 외부의 `X-Authenticated-User`·`X-Proxy-Secret`을 제거한 뒤 검증된 subject와 proxy secret을 새로 주입한다.
- `X-User-ID`는 development 전용이다. production에서 이 모드를 사용하면 application 생성이 실패한다.
- TLS는 ingress에서 종료하고 backend/DB/Redis/storage도 신뢰된 private network에 둔다.
- Local storage adapter는 개발/단일 host용이다. 다중 host 운영 전에는 암호화, checksum, versioning과 retry를 갖춘 S3/MinIO adapter가 필요하다.
- API의 process-local rate limiter 외에 ingress의 사용자/IP 기반 분산 rate limit를 반드시 둔다.

## 2. 배포 전 점검

```bash
git rev-parse HEAD
docker compose config
docker compose build
docker compose run --rm backend alembic upgrade head
docker compose up -d
python scripts/verify_deployment.py https://api.example.com
```

`alembic upgrade head`는 application 배포 전에 한 번만 실행한다. Backend image의 시작 명령도 migration을 실행하므로 rolling replica가 여러 개인 환경에서는 migration 전용 Job으로 분리한다. 배포 후 commit SHA, migration revision, image digest, `USE_MOCK_INFERENCE`, 활성 model key/version을 변경 기록에 남긴다.

현재 CI는 backend Ruff/mypy/pytest, frontend lint/Vitest/build, backend/frontend Docker build를 모두 실행한다. 실제 GPU adapter는 별도 GPU runner acceptance가 통과하기 전 production에 등록하지 않는다.

## 3. 설정과 보존 정책

| 설정 | 기본값 | 의미 |
|---|---:|---|
| `INPUT_RETENTION_HOURS` | 24 | 업로드 원본·정제 입력 만료 시간 |
| `OUTPUT_RETENTION_HOURS` | 168 | 생성 결과 만료 시간 |
| `CLEANUP_BATCH_SIZE` | 100 | cleanup 한 번의 종류별 최대 처리 수 |
| `ORPHAN_GRACE_HOURS` | 2 | DB에 없는 객체를 orphan으로 판단하기 전 유예 |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | 120 | API process별 방어 한도 |
| `READINESS_REQUIRE_REDIS` | false | readiness에서 Redis ping을 필수화할지 여부; compose는 true |

Voice Profile 샘플은 사용자가 프로필 또는 계정을 삭제할 때까지 유지한다. Job 입력은 `input_artifacts`가 원본·정제본과 owner·만료시각을 추적하며 Job 생성은 prefix가 아닌 이 row의 소유권과 만료를 검증한다. Job 출력은 생성 시 만료시각과 synthetic provenance를 기록한다. 이전 migration에서 생성되어 만료시각이 없는 출력은 `created_at + OUTPUT_RETENTION_HOURS` 규칙으로 정리한다.

## 4. Cleanup과 삭제 복구

Celery beat의 `voice.cleanup_expired`가 15분마다 다음 순서로 실행된다.

1. 만료 입력을 찾되 실행 중 Job이 참조하면 건너뛴다.
2. 만료 출력을 storage에서 지운 뒤 DB metadata를 삭제한다.
3. `DELETION_PENDING` Voice Profile 삭제를 재시도한다.
4. 삭제 요청되어 비활성화된 사용자 전체 데이터 삭제를 재시도한다.
5. `users/`, `voice-profiles/`, `jobs/` 아래에서 DB가 추적하지 않고 유예시간도 지난 객체만 orphan으로 삭제한다.

Storage 삭제가 실패하면 metadata를 먼저 지우지 않는다. 다음 실행에서 같은 exact key를 다시 삭제하므로 작업은 멱등이다. 오류 로그에는 key 원문 대신 SHA-256 앞 16자리만 남긴다. 수동 실행:

```bash
docker compose exec worker python -m app.workers.cleanup_worker
```

사용자 전체 삭제는 `DELETE /api/users/me`다. 먼저 사용자를 비활성화하고 활성 Job을 `CANCELLED`로 바꾸며 Queue revoke를 요청한다. 모든 profile sample, input original/cleaned, output 객체 삭제가 성공해야 user row를 삭제한다. 일부 storage 삭제가 실패하면 응답은 `202 {"status":"pending"}`이고 사용자는 `410`으로 차단되며 scheduler가 재시도한다. 다른 owner의 key는 수집 대상 query에 포함되지 않는다.

Profile 단독 삭제도 먼저 `DELETION_PENDING`을 commit한다. API 도중 storage 장애가 발생해도 scheduler가 sample key를 재조회해 마무리한다.

## 5. 로그와 trace

모든 HTTP 완료 로그에는 `request_id`, `trace_id`, method, route template, status, duration과 선택적 `actor_hash`가 JSON으로 기록된다. URL query와 request body는 기록하지 않는다. `traceparent`가 유효하면 trace ID를 이어받고 응답에 `X-Request-ID`, `X-Trace-ID`를 반환한다.

Redaction processor는 key 이름에 authorization, cookie, password, secret, token, input_text, audio, storage_key, external_id가 포함되면 값을 제거하며 bytes도 제거한다. 새 로그 field를 추가할 때 원문 사용자 식별자, 텍스트, 음성 bytes, storage key, 로컬 절대경로가 없는지 테스트한다. 오류 상세는 예외 type이나 안정된 error code만 기록한다.

## 6. Metrics와 dashboard

`GET /api/metrics`는 Prometheus text format을 반환한다. 이 endpoint는 ingress에서 public 접근을 막고 monitoring network에만 허용한다.

핵심 지표:

- `voice_http_requests_total{method,route,status}`: 요청량·오류율
- `voice_http_request_duration_seconds_{sum,count}{method,route}`: 평균 지연과 traffic 변화
- `voice_jobs{status}`: durable Job backlog와 실패 상태
- `voice_cleanup_pending{kind}`: input retry, profile, user 삭제 backlog
- `voice_cleanup_objects_total{result}`: 동일 process에서 관측한 cleanup 결과 확장점
- Job `metrics`: model loading/inference/postprocessing, GPU snapshot, CUDA OOM recovery

권장 dashboard panel과 alert:

| 신호 | 경고 기준 예시 | 확인 순서 |
|---|---|---|
| readiness | 2회 연속 503 | checks의 DB/storage/Redis 항목 |
| HTTP 5xx | 5분 비율 2% 초과 | route, request/trace ID, 최근 배포 |
| QUEUED | 10분 이상 증가 | Worker heartbeat, Redis, GPU admission |
| FAILED/CUDA_OOM | 5분 급증 | model/version, free VRAM, cache recovery |
| cleanup pending | 30분 이상 증가 | storage 권한/용량/네트워크 |
| disk usage | 80% 경고, 90% 긴급 | cleanup 실행, orphan 수, volume 확장 |

HTTP counter는 process-local이고 재시작 시 초기화된다. 여러 replica는 Prometheus가 replica별로 scrape·합산한다. Job/삭제 backlog gauge는 PostgreSQL에서 읽으므로 Worker와 API 사이의 공통 durable 상태를 보여준다.

## 7. 백업과 복구

PostgreSQL과 object storage를 같은 복구 시점으로 취급한다. 일관 백업이 필요하면 신규 upload/Job 생성을 maintenance mode로 막고 실행 중 Job이 끝난 뒤 시작한다.

```bash
pg_dump --format=custom --no-owner --file=voice-YYYYMMDD.dump "$DATABASE_URL_SYNC"
sha256sum voice-YYYYMMDD.dump > voice-YYYYMMDD.dump.sha256
```

Object storage는 provider의 versioned snapshot을 사용한다. Local volume이면 host snapshot 기능을 사용하며 live volume을 단순 tar로 복사하지 않는다. DB dump, storage snapshot ID, application commit, Alembic revision을 하나의 backup manifest에 기록한다. Redis 결과 backend는 복구 기준이 아니며 `jobs` DB가 기준이다.

복구는 production 원본에 바로 덮어쓰지 않는다.

```bash
createdb voice_restore_test
pg_restore --clean --if-exists --no-owner --dbname=voice_restore_test voice-YYYYMMDD.dump
DATABASE_URL=postgresql+asyncpg://.../voice_restore_test alembic current
```

별도 storage prefix에 snapshot을 복원하고 표본 `voice_samples`, `input_artifacts`, `job_outputs` key가 열리는지 검사한다. 삭제 요청 사용자와 만료 데이터가 되살아나지 않도록 복구 직후 cleanup을 실행한다. 분기별로 restore drill을 수행하고 RPO/RTO를 기록한다.

## 8. Rollback

1. 새 traffic을 중지하고 현재 image digest·DB revision·증상을 기록한다.
2. migration이 additive라면 이전 application image로 rollback한다. `0003`은 nullable user/output column과 새 table을 추가하므로 이전 app이 무시할 수 있다.
3. `python scripts/verify_deployment.py BASE_URL`과 핵심 API smoke test를 실행한다.
4. DB downgrade는 data loss 가능성이 있으므로 application rollback만으로 복구되지 않고 검증된 backup이 있을 때만 수행한다. `alembic downgrade`를 자동 rollback 단계에 넣지 않는다.
5. storage object를 수동으로 일괄 삭제하지 않는다. cleanup metadata와 backup manifest를 기준으로 복구한다.

## 9. 장애 Runbook

DB 장애: `/api/health`는 살아 있고 `/api/ready`의 database가 unavailable이다. 신규 traffic을 제거하고 DB failover/connection limit/disk를 확인한다. DB가 복구되면 pending Job과 cleanup backlog를 확인한다.

Redis 장애: readiness 설정이 true면 traffic에서 제거된다. 이미 실행 중인 Worker의 DB 상태는 보존된다. Redis 복구 뒤 `QUEUED`인데 `celery_task_id`가 없거나 오래된 Job을 조사하며 무조건 중복 enqueue하지 않는다.

Storage 장애: upload/download/cleanup을 중지하고 권한, disk, mount, object provider를 확인한다. metadata를 수동 삭제하지 않는다. 복구 뒤 cleanup을 실행해 delete retry와 orphan reconciliation을 확인한다.

GPU OOM: 해당 Job의 `CUDA_OOM`, `oom_recovery`, model cache metrics를 확인한다. Worker가 idle model과 CUDA cache를 정리한 뒤 다음 Mock/실제 canary Job이 성공하는지 확인한다. 반복되면 concurrency를 늘리지 말고 model VRAM 요구량, reserve, chunk size를 조정한다.

의심되는 abuse: 관련 Job/output/profile ID와 request/trace ID만 보존하고 음성 원문을 티켓에 첨부하지 않는다. `AbuseReportSink`는 moderation queue 연동 계약이며 기본 sink는 없다. 실제 접수 endpoint와 담당자/보존 정책이 연결되기 전에는 공개 서비스가 준비된 것으로 간주하지 않는다.

## 10. 생성물 안전 확장점

모든 새 Worker 출력은 `output_metadata.provenance`에 synthetic 표시, schema version, model key/version을 기록한다. 이는 감사·UI 표시에 사용할 provenance이고 비가청 watermark 자체는 아니다. 실제 watermark는 `WatermarkProvider`를 구현해 최종 파일이 storage에 publish되기 전에 적용하고, 검출 결과와 algorithm version을 provenance에 추가해야 한다. watermark 실패 시 원본을 publish하지 않는 fail-closed 정책을 권장한다.

`AbuseReportSink`는 output ID, reporter subject hash, category와 제한된 설명만 moderation system에 전달하는 port다. 원문 audio나 인증 token을 queue payload에 넣지 않는다.
