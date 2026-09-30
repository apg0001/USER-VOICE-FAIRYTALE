# Singing Voice Conversion Pipeline

Phase 7은 음악 입력을 보컬과 반주로 분리하고, 보컬에 등록된 Voice Profile의 음색을 적용한 뒤 기존 반주와 재합성하는 Worker 계약을 구현한다. 현재 구현은 오디오 흐름과 수명주기를 검증하는 CPU Mock이며 실제 가창 음색 품질을 제공하지 않는다.

## 입력 계약

`POST /api/files/inputs`에 `input_kind=singing`을 보내면 입력은 다음 preset으로 정규화된다.

- PCM S16LE WAV, 44.1 kHz, stereo
- 음악의 앞뒤와 다이내믹을 보존하기 위해 silence trim과 loudness normalize는 비활성
- noise reduction은 `off|normal|strong` 중 사용자가 선택
- 정제 key는 요청자 내부 ID 아래 `users/{user_id}/inputs/` namespace에 저장

Singing Job은 요청자 소유의 `READY` Voice Profile과 같은 사용자 namespace의 `input_storage_key`가 모두 필요하다.

## 처리 순서

```text
normalized WAV
  → 10초 PCM chunk
  → SeparationModel(vocal, instrumental)
  → SingingVoiceModel(converted vocal)
  → vocal RMS alignment
  → instrumental mix
  → -1 dBFS peak limiter
  → final WAV
```

`SeparationModel`과 `SingingVoiceModel`은 각각 독립된 Protocol과 registry를 사용한다. Worker가 두 모델을 load하고 역순으로 unload하므로 향후 분리 모델과 SVC 모델을 서로 독립적으로 교체할 수 있다.

각 chunk는 입력 byte 수를 보존해야 한다. Pipeline은 최종 frame 수, sample rate, channel 수와 duration을 검증하며 어댑터가 길이를 바꾸면 Job을 실패시킨다. checkpoint는 chunk 처리가 끝날 때마다 DB에 영속화된다.

## Mixing 정책

변환 보컬 RMS를 원래 보컬 RMS에 맞추되 gain은 `0.25–4.0` 범위로 제한한다. 보컬과 반주를 더한 뒤 peak가 -1 dBFS를 넘으면 전체 mix에 동일한 limiter gain을 적용한다. chunk별 vocal gain, limiter gain, limiter 전후 peak는 결과 manifest에 기록한다.

이 방식은 계약 검증을 위한 최소 정책이다. 실제 모델 도입 시에는 stem leakage, 위상, integrated loudness, 경계 cross-fade를 별도 음질 평가해야 한다.

## 저장과 정리

- Object Storage에는 최종 WAV만 `jobs/{job_id}/outputs/`에 publish한다.
- source staging, vocal, instrumental, converted vocal은 임시 디렉터리 또는 메모리에만 존재하며 Pipeline 종료 시 제거된다.
- 결과 metadata에는 source/final frame 수와 SHA-256, 세 stem의 SHA-256, stem의 `retained=false`, chunk별 mixing 지표를 저장한다.
- 실패 또는 완료 직전 취소 시 Worker는 자신이 publish한 최종 object와 `job_outputs` 행만 정리한다.

입력 업로드 시 보관되는 원본/정제본의 장기 보존기간과 orphan reconciliation은 Phase 10 과제다.

## Mock의 의미

- Mock separator는 각 sample의 60%를 vocal, 나머지를 instrumental로 나눠 두 stem 합이 입력과 같게 한다.
- Mock Singing 모델은 Voice Profile fingerprint에서 결정한 작은 gain 변화만 적용한다.
- 따라서 테스트할 수 있는 것은 frame/timing 보존, adapter lifecycle, 진행률, mixing limiter, metadata와 정리 계약이다.
- 화자 유사도, 음정 추적, 보컬 누설, 음악 품질을 검증했다는 뜻이 아니다.

## 검증 범위

- mono/stereo PCM 입력과 44.1 kHz stereo 전처리 preset
- 10초 chunk checkpoint의 단조 증가
- frame 수, channel, sample rate, duration 보존
- RMS alignment와 -1 dBFS clipping 방지
- stem hash와 비보존 표시
- Worker Job 완료와 최종 WAV publish

실제 모델 채택은 Phase 8 ModelManager 경계와 Issue #11의 라이선스·한국어 품질·GPU acceptance를 통과한 뒤 진행한다.
