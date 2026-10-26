# Job System

## 책임 경계

API는 Job row를 만들고 Queue에 `job_id`만 전달한다. 입력 텍스트와 storage key, 설정, 상태와 측정값은 PostgreSQL이 보유한다. Worker는 Job을 다시 조회한 뒤 모델을 실행하며 Celery result backend를 최종 상태로 사용하지 않는다.

개발 단계의 `X-User-ID`는 신뢰된 로컬 환경을 위한 임시 identity boundary다. 공개 운영 전 검증된 인증 token의 subject claim으로 교체해야 하며 외부 client가 임의 header를 선택할 수 있게 배포하면 안 된다.

## API

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/jobs` | Job 생성. `Idempotency-Key`가 같으면 기존 Job 반환 |
| GET | `/api/jobs` | 현재 소유자의 작업 목록. status/limit/offset 지원 |
| GET | `/api/jobs/{id}` | 상태, 진행률, ETA, Queue 위치 조회 |
| GET | `/api/jobs/{id}/events` | SSE 상태 snapshot, cursor와 heartbeat |
| POST | `/api/jobs/{id}/cancel` | 상태를 CANCELLED로 확정하고 cooperative revoke 요청 |
| POST | `/api/jobs/{id}/retry` | FAILED/CANCELLED 입력을 복제해 새 attempt 생성 |

다른 사용자의 Job은 존재 여부를 노출하지 않고 404로 응답한다. Queue 연결 실패는 Job을 `FAILED/QUEUE_UNAVAILABLE`로 기록한 뒤 503과 `job_id`를 반환한다. 내부 예외 상세는 API에 노출하지 않는다.

## 상태 머신

```text
QUEUED (0)
  → PREPROCESSING (1..29)
  → LOADING_MODEL (30..39)
  → INFERENCE (40..84)
  → POSTPROCESSING (85..99)
  → COMPLETED (100)
```

각 비종료 상태는 `FAILED` 또는 `CANCELLED`로 갈 수 있다. 완료 상태에서 다른 상태로의 전이는 금지된다. progress는 줄어들 수 없고 해당 단계 범위를 벗어날 수 없다. 이 규칙은 API나 Celery task가 아니라 `JobService` 한 곳에서 적용한다.

## 멱등성과 재시도

`(user_id, idempotency_key)` unique constraint가 client retry와 동시 요청의 중복 Job을 막는다. Celery task ID도 Job UUID로 고정한다. 재시도는 기존 row를 되살리지 않고 `retry_of_job_id`, 증가한 `attempt`를 가진 새 Job을 생성해 감사 가능성을 유지한다.

Celery는 `acks_late`, prefetch 1, Worker concurrency 1을 기본으로 한다. Worker가 같은 task를 다시 받으면 DB status가 `QUEUED`인지 확인하고 이미 시작되거나 끝난 작업은 실행하지 않는다.

## 진행률, Queue 위치와 ETA

- Queue 위치는 현재 QUEUED Job의 생성 순서로 계산한다.
- 측정 callback이 없는 Mock/모델은 단계 시작 progress만 기록한다.
- ETA는 같은 mode/model의 완료 이력에서 `processing_time / input_duration` 중앙값을 사용한다.
- 유효한 이력이 3개 미만이면 추측값 대신 `null`을 반환한다.

## SSE와 polling fallback

- SSE endpoint는 연결 전에 Job 소유권을 검사하며 다른 사용자에게는 404를 반환한다.
- 각 상태 조회는 새롭고 짧은 DB session을 사용해 streaming 연결이 transaction을 장시간 점유하지 않는다.
- event payload는 일반 Job 조회와 같은 schema이며 `id`, `event: job`, JSON `data`를 포함한다.
- reconnect의 `Last-Event-ID` 다음 번호부터 event ID를 이어간다. 영속 event log를 재생하는 방식은 아니며 reconnect 직후 최신 전체 snapshot을 다시 보낸다.
- 상태 변화가 없으면 기본 15초마다 comment heartbeat를 보낸다.
- terminal 상태를 전송하면 server stream을 닫는다.
- Browser는 인증 header가 필요한 개발 identity 때문에 native `EventSource` 대신 fetch stream을 사용한다.
- stream EOF/오류 시 cursor를 포함해 두 번 재연결하고, 계속 실패하면 1초 polling으로 전환한다. polling도 일시 실패하면 계속 재시도하며 연결 상태를 표시한다.
- Nginx의 events location은 buffering/cache를 끄고 read timeout을 1시간으로 둔다.

## 장애 처리

- Queue dispatch 실패: 해당 Job만 FAILED 처리하고 API는 503 반환
- 모델/전처리 실패: 해당 Job만 FAILED 처리하고 Worker는 다음 task 처리
- 취소: DB를 먼저 CANCELLED로 commit하고 Celery revoke는 `terminate=False`
- Worker 중복 전달: terminal/non-QUEUED 상태 확인으로 no-op
- OOM: `CUDA_OOM`으로 현재 Job만 실패시키고 cache 정리 후 Worker가 다음 task를 처리

API process가 DB commit 직후 broker 전송 전에 종료되는 좁은 구간을 위한 queued-job reconciliation은 운영 안정성 Phase에서 주기 dispatcher/outbox로 보강한다. 현재도 해당 Job은 DB에 QUEUED로 남아 수동 복구가 가능하며 데이터가 유실되지는 않는다.

