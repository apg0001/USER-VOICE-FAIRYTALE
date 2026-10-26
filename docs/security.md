# 음성 안전 및 개인정보 원칙

Voice Fairy Tale은 음성과 파생 speaker embedding을 민감한 사용자 데이터로 취급한다. 기능 편의보다 동의, 추적 가능한 삭제, 최소 보관을 우선한다.

## 등록 동의

- 사용자는 등록 음성이 본인의 음성이거나 명시적 사용 권한이 있음을 확인해야 한다.
- 동의 문구 버전과 동의 시각을 `voice_profiles`에 기록한다.
- 타인을 사칭하거나 동의 없이 제3자의 음성을 복제하는 사용을 금지한다.
- Voice Profile 생성 API는 현재 동의 버전과 권한 선언을 오디오 처리 전에 필수로 검증한다.
- 구현된 등록·조회·삭제 계약은 [Voice Profile 문서](voice-profiles.md)를 따른다.

## 업로드 방어

- 허용 확장자와 실제 MIME/컨테이너를 함께 검증한다.
- ffprobe를 격리된 프로세스로 실행하고 시간·메모리 제한을 둔다.
- 사용자 파일명을 저장 경로로 사용하지 않고 UUID storage key를 발급한다.
- 압축 폭탄, 경로 순회, 손상 미디어, 크기·길이 제한 초과를 거부한다.

## 데이터 수명주기

- 원본, 정제본, embedding, 중간 파일, 결과 파일을 구분하여 추적한다.
- 임시 파일은 Job 종료 후 즉시, 디버그 보관본은 설정된 기간 후 삭제한다.
- Singing Pipeline의 vocal/instrumental/converted vocal은 임시 메모리에서만 처리하고 최종 WAV만 publish한다. stem hash와 `retained=false` 표시는 결과 manifest에 남긴다.
- Voice Profile 삭제 시 먼저 `DELETION_PENDING`을 기록하고 원본·정제본·샘플 metadata·프로필을 연쇄 삭제한다. 저장소 삭제 실패 시 metadata를 남겨 재시도한다.
- 로그에는 토큰, 비밀번호, 원본 텍스트 전문, 음성 바이트, 로컬 절대 경로를 남기지 않는다.

Phase 10부터 Job 입력 원본·정제본은 `input_artifacts`에서 owner와 만료시각을 추적하고, 결과는 `job_outputs.expires_at`으로 보존기간을 적용한다. Celery scheduler는 삭제 실패 metadata를 유지한 채 재시도하며 유예시간을 지난 알려진 namespace의 orphan만 정리한다. `DELETE /api/users/me`는 활성 Job 취소 후 profile/sample/input/output을 owner 범위로 삭제하고 일부 실패 시 계정을 비활성 상태로 유지한다.

## 인증과 요청 경계

- development의 `X-User-ID`는 로컬 테스트 전용이다.
- production은 `AUTH_MODE=trusted_proxy`가 아니면 시작하지 않으며, 공유 secret과 gateway가 검증한 subject를 모두 요구한다.
- gateway는 외부가 보낸 identity/proxy header를 제거하고 backend network를 비공개로 제한해야 한다.
- application rate limit는 process별 방어선이다. 공개 운영은 ingress의 분산 사용자/IP rate limit를 함께 사용한다.
- 요청 로그는 route template과 subject hash만 기록하며 body, query, token, storage key를 기록하지 않는다.

## 위협 모델

| 위협 | 현재 통제 | 잔여 위험/필수 운영 조치 |
|---|---|---|
| 타인 음성 무단 등록 | 동의 버전·권리 선언을 처리 전에 검증 | 선언 진위 검증과 abuse moderation 필요 |
| 다른 owner 객체 접근 | DB join/owner row로 profile, input, job, output 검사 | 관리자 도구도 동일 범위 검증 필요 |
| header spoofing | production trusted proxy secret·network 경계 | secret rotation과 gateway header strip 필요 |
| path traversal/악성 media | UUID key, safe path, magic/MIME, ffprobe 제한 | ffmpeg sandbox/seccomp 강화 필요 |
| 민감 로그 유출 | 구조화 로그 redaction, body/query 미기록 | log backend 접근 통제·보존기간 필요 |
| 삭제 중 storage 장애 | pending metadata, 멱등 retry, orphan 유예 | provider versioning의 삭제 marker 정책 검토 |
| 대량 요청/GPU 고갈 | API rate limit, Queue, GPU admission, concurrency 1 | ingress 분산 limit와 quota 필요 |
| 생성물 오인·악용 | synthetic provenance, watermark/abuse port | 실제 watermark provider와 moderation sink 미연결 |

## 공개 운영 전 남은 통제

현재 trusted proxy 인증 경계, 요청 rate limit, redacted audit-style 로그, synthetic provenance는 구현되어 있다. 그러나 실제 IdP/gateway 배포, 저장 암호화, 관리자 권한 모델, abuse report sink, 비가청 watermark provider, 법률 검토는 환경별로 연결해야 한다. 이 통제가 완료되기 전에는 불특정 다수에게 Voice Cloning 기능을 공개하지 않는다. 세부 운영 절차는 [운영 Runbook](operations.md)을 따른다.

