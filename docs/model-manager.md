# GPU Model Manager와 OOM 복구

Phase 8은 Worker가 제한된 GPU 메모리에서 여러 모델을 안전하게 재사용하고, 한 Job의 CUDA OOM이 Worker 전체 장애로 확산되지 않게 하는 수명주기 경계를 구현한다. API process는 PyTorch/CUDA를 import하지 않는다.

## Process 소유권

- Celery Worker process 하나가 `CUDA_DEVICE` 하나를 소유한다.
- 기본 Worker concurrency와 prefetch는 각각 1이다.
- `ModelManager` singleton은 Worker process 안에만 존재한다.
- process 종료 signal과 `atexit`에서 idle model을 unload하고 CUDA cache를 비운다.
- CPU/Mock mode에서는 PyTorch를 import하지 않는다.

## Cache와 lease

Cache key는 `(role, model key, version, device)`다. 같은 stable key를 쓰더라도 TTS, Speech VC, Singing VC adapter가 충돌하지 않도록 role을 포함한다.

```text
factory → descriptor 확인 → per-key lock → cache hit 또는 admission/load
                                         ↓
                         lease reference 증가 → inference
                                         ↓
                         lease 종료 → reference 감소/LRU 갱신
```

- 최초 요청에서만 lazy load한다.
- adapter factory가 만드는 객체의 constructor는 자원을 할당하지 않아야 하며 GPU/파일 자원은 반드시 `load()`에서 할당한다.
- 같은 key의 동시 요청은 lifecycle lock으로 중복 load를 막는다.
- lease reference가 1 이상인 model은 eviction하지 않는다.
- cache가 `MODEL_CACHE_LIMIT`에 도달하면 가장 오래 사용하지 않은 idle model을 unload한다.
- Singing Job은 separator와 SVC를 동시에 lease하므로 기본 limit는 2다.
- model load time, cache hit/miss, eviction key, device snapshot은 Job metrics에 저장한다.

## VRAM admission

GPU model descriptor는 `metadata.estimated_vram_mb`를 제공해야 한다. load 전에 다음을 확인한다.

1. 설정한 CUDA device가 사용 가능한지 확인한다.
2. `required_vram_mb + GPU_VRAM_RESERVE_MB` 이상의 free VRAM이 있는지 확인한다.
3. 부족하면 idle LRU model을 unload하고 CUDA cache를 비운 뒤 다시 측정한다.
4. 여전히 부족하면 model을 load하지 않고 `VRAM_ADMISSION_FAILED`로 현재 Job만 실패시킨다.

GPU를 요구하는데 PyTorch/CUDA/device가 없으면 `GPU_UNAVAILABLE`, 모든 cache entry가 lease 중이면 `MODEL_CAPACITY_EXCEEDED`를 기록한다. 추정치는 admission 방어선이지 실제 peak VRAM 보장이 아니므로 실제 runner 측정으로 갱신해야 한다.

## CUDA OOM 복구

Model load 또는 inference에서 CUDA OOM을 감지하면 다음 순서를 지킨다.

1. 현재 Job의 model lease를 모두 해제한다.
2. 현재 model을 포함한 모든 idle cache entry를 unload한다.
3. Python garbage collection과 `torch.cuda.empty_cache()`를 실행한다.
4. device 상태를 다시 probe해 `oom_recovery` Job metric에 남긴다.
5. 현재 Job만 `FAILED/CUDA_OOM`으로 종료한다.
6. Worker process는 유지되어 다음 Job을 처리한다.

CPU fake runtime 통합 테스트는 첫 Job에서 inference OOM을 발생시키고, cache 정리 후 같은 Worker manager로 두 번째 Job이 완료되는 것을 검증한다.

## 진단 정보

실제 mode Worker 시작 로그와 `python -m app.workers.gpu_check`는 다음 필드만 출력한다.

- configured device와 availability
- GPU name
- total/free VRAM MiB
- PyTorch version과 CUDA runtime version
- 실패 시 비민감 reason code

모델 경로, 음성 데이터, 입력 text, token과 로컬 사용자 경로는 기록하지 않는다.

## GPU 없는 CI

일반 CI는 PyTorch를 설치하지 않는다. 다음을 fake runtime/model로 검증한다.

- CPU mode에서 PyTorch 미import
- lazy load와 cache hit
- 동시 요청의 단일 load
- active lease eviction 방지와 idle LRU eviction
- GPU unavailable/VRAM 부족 사전 거부
- load OOM과 inference OOM 정리
- OOM 직후 다음 Job 성공

이 테스트는 실제 CUDA allocator, driver, fragmentation 동작을 증명하지 않는다.

## 실제 GPU runner 검증 절차

사전 조건:

- Linux GPU host, NVIDIA driver와 Container Toolkit
- CUDA/PyTorch가 고정된 별도 Worker image
- 라이선스와 weights provenance가 승인된 실제 adapter
- 공개 사용자 데이터가 아닌 고정 내부 fixture

진단:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm \
  worker python -m app.workers.gpu_check
```

그 다음 model별로 cold load, warm cache hit, LRU 교체, 의도적으로 낮춘 admission 한도, 격리된 OOM fixture, OOM 직후 정상 Job을 실행한다. 각 실행에서 GPU/driver/image digest, model/weights version, load time, peak allocated/reserved VRAM, RTF, Job 상태와 Worker 생존 여부를 기록한다.

현재 repository의 기본 Worker image에는 PyTorch와 실제 model adapter가 없으므로 위 진단은 GPU acceptance 환경을 준비한 뒤 실행한다. 실행하지 않은 실제 GPU 검증을 완료로 표기하지 않는다.
