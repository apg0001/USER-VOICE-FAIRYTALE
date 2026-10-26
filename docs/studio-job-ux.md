# Voice Studio Job UX

Phase 9는 작업 생성 이후 사용자가 진행 상태를 놓치지 않고 완료 결과까지 다룰 수 있는 한 화면 흐름을 제공한다.

## 사용자 흐름

```text
Voice Profile 선택 → Text/Audio 입력 → Job 생성
  → SSE 진행 snapshot → 완료 → 인증된 blob fetch → 재생/다운로드
                     ├→ 취소
                     └→ 실패/취소 → 새 attempt로 재시도
```

페이지 진입 시 최근 50개 작업을 읽고 가장 최근의 non-terminal Job monitoring을 재개한다. 이력에는 mode, 생성 시각, attempt, status, progress, Queue 위치와 근거가 있는 ETA만 표시한다. ETA가 `null`이면 임의 값을 만들지 않는다.

## 진행 연결

`jobMonitor.ts`는 fetch streaming으로 SSE를 읽는다. `X-User-ID`와 `Last-Event-ID`를 보낼 수 있어 native EventSource 대신 사용한다.

1. SSE를 우선 연결한다.
2. stream이 terminal event 없이 끊기면 마지막 event ID로 최대 두 번 재연결한다.
3. 재연결이 계속 실패하면 기존 Job GET polling으로 전환한다.
4. polling의 일시 오류는 offline 상태로 표시하고 계속 재시도한다.
5. terminal status를 받거나 component가 unmount되면 AbortController로 연결을 정리한다.

SSE event ID는 reconnect 순서를 위한 connection cursor다. 서버에 영속 event log는 없으므로 과거 모든 전이를 재생한다고 가정하지 않고 최신 전체 Job snapshot을 멱등 적용한다.

## 취소와 재시도

- 진행 중 Job은 취소할 수 있다. DB terminal 상태를 먼저 확정하고 Worker는 chunk/stage 경계에서 협력적으로 중단한다.
- FAILED/CANCELLED Job은 원본 row를 되살리지 않고 `attempt + 1`, `retry_of_job_id`를 가진 새 Job으로 재시도한다.
- UI는 요청 중 중복 생성을 막고 backend는 `Idempotency-Key`로 생성 중복 dispatch를 막는다.

## 결과 재생과 다운로드

결과 URL을 `<audio src>`에 직접 넣지 않는다. 소유권 header를 포함해 blob을 fetch한 뒤 object URL을 audio player에 연결한다. 교체·unmount 때 object URL을 revoke한다. 다운로드도 같은 인증 fetch 뒤 임시 anchor로 수행한다.

## 접근성과 오류 상태

- mode 선택 button은 `aria-pressed`를 제공한다.
- progress는 label이 있는 native `progress` element다.
- 상태 연결 문구는 `aria-live=polite`, Job 오류는 `role=alert`로 제공한다.
- 취소·재시도·재생·다운로드 button은 mode가 포함된 구체적 accessible name을 가진다.
- 이력이 없을 때 빈 상태를 표시한다.
- 모든 핵심 동작은 native button/select/input으로 키보드 조작할 수 있다.

## 테스트 범위

Backend는 소유권 404, terminal snapshot, `Last-Event-ID`, heartbeat와 상태 변화 후 종료를 검증한다. Frontend Vitest는 SSE parsing, cursor reconnect, polling fallback, 빈 이력, progress/Queue/ETA, 취소·재시도·재생·다운로드 동작과 accessible name을 검증한다.

현재 `X-User-ID: local-developer`는 개발용 identity일 뿐 인증이 아니다. 공개 운영 전 실제 인증 token으로 교체해야 한다.
