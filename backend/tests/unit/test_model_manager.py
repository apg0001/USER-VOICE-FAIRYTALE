from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, Lock

import pytest

from app.core.gpu import GPUSnapshot
from app.models.base import ModelCapability, ModelDescriptor
from app.models.manager import (
    GPUUnavailableError,
    ModelCapacityError,
    ModelManager,
    ModelOutOfMemoryError,
    VRAMAdmissionError,
)


class FakeCudaOutOfMemoryError(RuntimeError):
    pass


@dataclass
class FakeRuntime:
    available: bool = True
    free_memory_mb: int = 8_000
    empty_cache_calls: int = 0

    def probe(self, device: str) -> GPUSnapshot:
        return GPUSnapshot(
            device=device,
            available=self.available,
            name="Fake GPU" if self.available else None,
            total_memory_mb=16_000 if self.available else None,
            free_memory_mb=self.free_memory_mb if self.available else None,
            reason=None if self.available else "fake-unavailable",
        )

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeCudaOutOfMemoryError)

    def empty_cache(self, device: str) -> None:
        self.empty_cache_calls += 1


class FakeModel:
    def __init__(
        self,
        key: str,
        *,
        requires_gpu: bool = False,
        estimated_vram_mb: int = 0,
        fail_load: bool = False,
        on_unload: object | None = None,
    ) -> None:
        self.descriptor = ModelDescriptor(
            key=key,
            display_name=key,
            version="1.0",
            capabilities=(ModelCapability.GENERAL_TTS,),
            requires_gpu=requires_gpu,
            metadata={"estimated_vram_mb": estimated_vram_mb},
        )
        self.fail_load = fail_load
        self.on_unload = on_unload
        self.load_count = 0
        self.unload_count = 0

    def load(self) -> None:
        self.load_count += 1
        if self.fail_load:
            raise FakeCudaOutOfMemoryError("simulated")

    def unload(self) -> None:
        self.unload_count += 1
        if callable(self.on_unload):
            self.on_unload()


def build_manager(
    runtime: FakeRuntime, *, limit: int = 2, reserve_mb: int = 500
) -> ModelManager:
    return ModelManager(
        device="cuda:0",
        max_cached_models=limit,
        vram_reserve_mb=reserve_mb,
        gpu_runtime=runtime,
    )


def test_manager_lazy_loads_once_and_reports_cache_hit() -> None:
    runtime = FakeRuntime()
    manager = build_manager(runtime)
    created: list[FakeModel] = []

    def factory() -> FakeModel:
        model = FakeModel("voice")
        created.append(model)
        return model

    with manager.lease("tts", factory) as first:
        assert first.cache_hit is False
        assert first.model.load_count == 1
    with manager.lease("tts", factory) as second:
        assert second.cache_hit is True
        assert second.model is first.model

    assert len(created) == 2
    assert created[0].load_count == 1
    assert created[1].load_count == 0


def test_manager_lru_evicts_only_idle_models() -> None:
    manager = build_manager(FakeRuntime(), limit=2)
    models: dict[str, FakeModel] = {}

    def factory(key: str) -> FakeModel:
        model = FakeModel(key)
        models.setdefault(key, model)
        return model

    with manager.lease("tts", lambda: factory("a")):
        pass
    with manager.lease("tts", lambda: factory("b")):
        pass
    with manager.lease("tts", lambda: factory("a")):
        pass
    with manager.lease("tts", lambda: factory("c")) as lease:
        assert lease.evicted[0].startswith("tts:b@1.0")

    assert models["b"].unload_count == 1
    assert {key.model_key for key in manager.cached_keys()} == {"a", "c"}


def test_manager_rejects_capacity_when_every_entry_is_leased() -> None:
    manager = build_manager(FakeRuntime(), limit=1)

    with (
        manager.lease("tts", lambda: FakeModel("a")),
        pytest.raises(ModelCapacityError),
        manager.lease("tts", lambda: FakeModel("b")),
    ):
        pass


def test_manager_serializes_duplicate_loads() -> None:
    manager = build_manager(FakeRuntime(), limit=1)
    creation_lock = Lock()
    created: list[FakeModel] = []
    barrier = Barrier(2)

    def factory() -> FakeModel:
        model = FakeModel("shared")
        with creation_lock:
            created.append(model)
        return model

    def use_model() -> int:
        with manager.lease("tts", factory) as lease:
            barrier.wait(timeout=2)
            return lease.model.load_count

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: use_model(), range(2)))

    assert results == [1, 1]
    assert sum(model.load_count for model in created) == 1


def test_gpu_admission_checks_availability_and_free_memory() -> None:
    unavailable = build_manager(FakeRuntime(available=False))
    with pytest.raises(GPUUnavailableError), unavailable.lease(
        "tts", lambda: FakeModel("gpu", requires_gpu=True, estimated_vram_mb=1_000)
    ):
        pass


def test_gpu_admission_evicts_idle_model_before_loading() -> None:
    runtime = FakeRuntime(free_memory_mb=2_500)
    manager = build_manager(runtime, limit=2, reserve_mb=500)
    first = FakeModel(
        "first",
        requires_gpu=True,
        estimated_vram_mb=1_000,
        on_unload=lambda: setattr(runtime, "free_memory_mb", 3_000),
    )
    with manager.lease("tts", lambda: first):
        pass
    runtime.free_memory_mb = 1_200

    with manager.lease(
        "tts",
        lambda: FakeModel("second", requires_gpu=True, estimated_vram_mb=1_000),
    ) as lease:
        assert lease.evicted[0].startswith("tts:first@1.0")

    assert first.unload_count == 1

    insufficient = build_manager(FakeRuntime(free_memory_mb=1_200), reserve_mb=500)
    with pytest.raises(VRAMAdmissionError), insufficient.lease(
        "tts", lambda: FakeModel("gpu", requires_gpu=True, estimated_vram_mb=1_000)
    ):
        pass


def test_oom_during_load_is_cleaned_and_next_model_can_run() -> None:
    runtime = FakeRuntime()
    manager = build_manager(runtime, limit=1)
    failed = FakeModel("oom", requires_gpu=True, fail_load=True)

    with pytest.raises(ModelOutOfMemoryError), manager.lease("tts", lambda: failed):
        pass

    assert failed.unload_count == 1
    assert runtime.empty_cache_calls == 1
    assert manager.cached_keys() == ()
    with manager.lease("tts", lambda: FakeModel("healthy")) as healthy:
        assert healthy.model.load_count == 1


def test_recover_after_inference_oom_unloads_affected_idle_model() -> None:
    runtime = FakeRuntime()
    manager = build_manager(runtime)

    with manager.lease("tts", lambda: FakeModel("voice")) as lease:
        cache_key = lease.cache_key
    snapshot = manager.recover_after_oom({cache_key})

    assert snapshot.available is True
    assert runtime.empty_cache_calls == 1
    assert manager.cached_keys() == ()
