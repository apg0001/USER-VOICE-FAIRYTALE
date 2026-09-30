# Voice Profile과 동의 수명주기

Voice Profile은 사용자가 사용 권한을 확인한 음성 샘플과 모델별 파생 프로필을 묶는 소유권 경계다. 등록 API는 동의를 오디오 처리보다 먼저 검증하며, 다른 사용자의 프로필 존재 여부를 노출하지 않는다.

## 등록 흐름

```text
현재 동의문 조회
  → 동의·권한 선언
  → 업로드 크기 제한
  → 형식/컨테이너 검사
  → ffprobe와 품질 분석
  → 원본·정제본 분리 저장
  → 최소 유효 발화 10초 검사
  → 모델별 profile builder
  → READY
```

`POST /api/voices`는 `multipart/form-data` 요청을 받는다. `consent_accepted`, `owns_voice_or_has_permission`가 모두 참이어야 하고, 클라이언트가 제출한 `consent_version`은 `GET /api/voices/consent`가 반환하는 현재 버전과 같아야 한다. 조건을 만족하지 않으면 임시 업로드를 제외한 음성 처리를 시작하지 않는다.

프로필은 처리 중 `PROCESSING`, 성공 시 `READY`, 품질 또는 처리 실패 시 `REJECTED`가 된다. Job은 요청 사용자 소유이면서 `READY`인 프로필만 참조할 수 있다.

## 저장 데이터

| 위치 | 데이터 | 목적 |
|---|---|---|
| `voice_profiles` | 소유자, 이름, 상태, 동의 버전·시각 | 동의와 소유권의 기준 |
| profile metadata | 동의 선언, probe, 전처리 설정, SHA-256, 모델별 파생 프로필 | 처리 재현성과 파생 데이터 추적 |
| `voice_samples` | 원본·정제본 storage key, 길이, sample rate, 품질 지표 | 샘플 수명주기 추적 |
| Object Storage | UUID key의 원본과 정제 WAV | 바이너리를 DB와 분리 |

원래 파일명은 검증에만 사용하며 저장 경로에 포함하지 않는다. API 응답은 storage key나 원본 파일 경로를 노출하지 않는다.

## 모델별 Profile Builder

`ProfileBuilderRegistry`는 오디오 전처리와 모델별 speaker profile 생성을 분리한다. builder는 안정적인 모델 key를 가지며 같은 `AudioArtifact`에서 embedding, reference manifest 또는 모델 전용 캐시를 만들 수 있다. 현재 CPU/CI 환경의 `MockVoiceProfileBuilder`는 계약 검증용 metadata만 생성하며 실제 speaker embedding을 만들지 않는다.

실제 builder를 추가할 때는 다음을 지킨다.

- API 프로세스에서 GPU 모델을 로드하지 않고 Worker 경계 안에서 실행한다.
- 생성한 모든 객체 key를 profile metadata에 기록해 삭제할 수 있게 한다.
- 모델 버전과 입력 SHA-256을 포함해 오래된 파생 프로필을 판별한다.
- 원본 음성이나 embedding을 로그에 기록하지 않는다.

## 조회와 삭제

`GET /api/voices`와 `GET /api/voices/{id}`는 `X-User-ID`로 해석한 현재 사용자의 프로필만 반환한다. 존재하지 않거나 다른 사용자 소유인 ID는 모두 `404`다.

`DELETE /api/voices/{id}`는 먼저 상태를 `DELETION_PENDING`으로 기록한 뒤 정제본, 원본, 샘플 metadata, 프로필 순으로 삭제한다. Object Storage 삭제가 실패하면 DB 프로필을 남겨 재시도할 수 있게 한다. 삭제가 완료되면 프로필 조회 결과에서 즉시 사라진다. 향후 실제 모델 builder가 별도 객체를 만들면 동일 삭제 경로에 등록해야 한다.

## API 예시

현재 동의문:

```bash
curl http://localhost:8000/api/voices/consent
```

프로필 등록:

```bash
curl -X POST http://localhost:8000/api/voices \
  -H "X-User-ID: local-developer" \
  -F "name=내 이야기 목소리" \
  -F "consent_accepted=true" \
  -F "owns_voice_or_has_permission=true" \
  -F "consent_version=2026-09-01" \
  -F "noise_reduction=normal" \
  -F "voice_sample=@sample.wav;type=audio/wav"
```

조회와 삭제:

```bash
curl -H "X-User-ID: local-developer" http://localhost:8000/api/voices
curl -X DELETE -H "X-User-ID: local-developer" \
  http://localhost:8000/api/voices/PROFILE_ID
```

`X-User-ID`는 현재 개발용 신뢰 경계다. 공개 운영 전에 검증된 인증 토큰의 subject로 교체하고 감사 로그, 암호화, rate limit, abuse 대응을 적용해야 한다.
