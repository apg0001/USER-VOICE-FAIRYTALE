import gc
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from threading import Lock, RLock
from typing import Generic, Protocol, TypeVar, cast

from app.core.gpu import GPURuntime, GPUSnapshot
from app.models.base import ModelDescriptor


class LifecycleModel(Protocol):
    descriptor: ModelDescriptor

    def load(self) -> None: ...

    def unload(self) -> None: ...


class ModelManagerError(RuntimeError):
    pass


class GPUUnavailableError(ModelManagerError):
    pass


class VRAMAdmissionError(ModelManagerError):
    pass


class ModelCapacityError(ModelManagerError):
    pass


class ModelOutOfMemoryError(ModelManagerError):
    pass


@dataclass(frozen=True, slots=True)
class ModelCacheKey:
    role: str
    model_key: str
    version: str
    device: str

    def label(self) -> str:
        return f"{self.role}:{self.model_key}@{self.version}:{self.device}"


@dataclass(slots=True)
class _CacheEntry:
    model: LifecycleModel
    references: int
    last_used: float
    gpu_snapshot: GPUSnapshot


ModelT = TypeVar("ModelT", bound=LifecycleModel)


@dataclass(frozen=True, slots=True)
class ModelLease(Generic[ModelT]):
    model: ModelT
    cache_key: ModelCacheKey
    cache_hit: bool
    load_seconds: float
    evicted: tuple[str, ...]
    gpu: GPUSnapshot

    def metrics(self) -> dict[str, object]:
        return {
            "cache_key": self.cache_key.label(),
            "cache_hit": self.cache_hit,
            "load_seconds": round(self.load_seconds, 6),
            "evicted": list(self.evicted),
            "gpu": self.gpu.to_dict(),
        }


class ModelManager:
    def __init__(
        self,
        *,
        device: str,
        max_cached_models: int,
        vram_reserve_mb: int,
        gpu_runtime: GPURuntime,
    ) -> None:
        if max_cached_models < 1:
            raise ValueError("max_cached_models must be at least 1")
        if vram_reserve_mb < 0:
            raise ValueError("vram_reserve_mb cannot be negative")
        self.device = device
        self.max_cached_models = max_cached_models
        self.vram_reserve_mb = vram_reserve_mb
        self.gpu_runtime = gpu_runtime
        self._entries: dict[ModelCacheKey, _CacheEntry] = {}
        self._key_locks: dict[ModelCacheKey, Lock] = {}
        self._state_lock = RLock()

    @contextmanager
    def lease(
        self,
        role: str,
        factory: Callable[[], ModelT],
    ) -> Iterator[ModelLease[ModelT]]:
        candidate = factory()
        descriptor = candidate.descriptor
        cache_key = ModelCacheKey(role, descriptor.key, descriptor.version, self.device)
        key_lock = self._lock_for(cache_key)
        evicted: tuple[str, ...] = ()
        load_seconds = 0.0
        cache_hit = False
        snapshot = GPUSnapshot(
            device=self.device,
            available=False,
            reason="gpu-not-required",
        )

        with key_lock, self._state_lock:
            entry = self._entries.get(cache_key)
            if entry is None:
                required_vram_mb = self._required_vram_mb(descriptor)
                if descriptor.requires_gpu:
                    snapshot = self.gpu_runtime.probe(self.device)
                evicted = self._admit(
                    requires_gpu=descriptor.requires_gpu,
                    required_vram_mb=required_vram_mb,
                    initial_snapshot=snapshot,
                )
                started = time.monotonic()
                try:
                    candidate.load()
                except Exception as error:
                    with suppress(Exception):
                        candidate.unload()
                    if self.gpu_runtime.is_out_of_memory(error):
                        gc.collect()
                        self.gpu_runtime.empty_cache(self.device)
                        raise ModelOutOfMemoryError(cache_key.label()) from error
                    raise
                load_seconds = time.monotonic() - started
                if descriptor.requires_gpu:
                    snapshot = self.gpu_runtime.probe(self.device)
                entry = _CacheEntry(
                    candidate,
                    references=0,
                    last_used=time.monotonic(),
                    gpu_snapshot=snapshot,
                )
                self._entries[cache_key] = entry
            else:
                cache_hit = True
                snapshot = entry.gpu_snapshot
            entry.references += 1
            entry.last_used = time.monotonic()
            managed_model = cast(ModelT, entry.model)

        try:
            yield ModelLease(
                model=managed_model,
                cache_key=cache_key,
                cache_hit=cache_hit,
                load_seconds=load_seconds,
                evicted=evicted,
                gpu=snapshot,
            )
        finally:
            with self._state_lock:
                current = self._entries.get(cache_key)
                if current is not None:
                    current.references = max(0, current.references - 1)
                    current.last_used = time.monotonic()

    def recover_after_oom(self, cache_keys: set[ModelCacheKey]) -> GPUSnapshot:
        with self._state_lock:
            recovery_order = [
                *cache_keys,
                *(key for key in self._entries if key not in cache_keys),
            ]
            for cache_key in recovery_order:
                entry = self._entries.get(cache_key)
                if entry is None or entry.references > 0:
                    continue
                self._unload(cache_key, entry, suppress_errors=True)
        gc.collect()
        self.gpu_runtime.empty_cache(self.device)
        return self.gpu_runtime.probe(self.device)

    def shutdown(self) -> None:
        with self._state_lock:
            for cache_key, entry in list(self._entries.items()):
                if entry.references == 0:
                    self._unload(cache_key, entry, suppress_errors=True)
        gc.collect()
        self.gpu_runtime.empty_cache(self.device)

    def cached_keys(self) -> tuple[ModelCacheKey, ...]:
        with self._state_lock:
            return tuple(self._entries)

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, ModelOutOfMemoryError) or self.gpu_runtime.is_out_of_memory(
            error
        )

    def diagnose(self) -> GPUSnapshot:
        return self.gpu_runtime.probe(self.device)

    def _lock_for(self, cache_key: ModelCacheKey) -> Lock:
        with self._state_lock:
            return self._key_locks.setdefault(cache_key, Lock())

    def _admit(
        self,
        *,
        requires_gpu: bool,
        required_vram_mb: int,
        initial_snapshot: GPUSnapshot,
    ) -> tuple[str, ...]:
        if requires_gpu and not initial_snapshot.available:
            raise GPUUnavailableError(initial_snapshot.reason or "GPU unavailable")
        evicted: list[str] = []
        while len(self._entries) >= self.max_cached_models:
            victim = self._oldest_idle_entry()
            if victim is None:
                raise ModelCapacityError("all cached models are currently leased")
            cache_key, entry = victim
            evicted.append(cache_key.label())
            self._unload(cache_key, entry)

        if requires_gpu and required_vram_mb > 0:
            snapshot = self.gpu_runtime.probe(self.device)
            while not self._has_vram(snapshot, required_vram_mb):
                victim = self._oldest_idle_entry()
                if victim is None:
                    raise VRAMAdmissionError(
                        f"requires {required_vram_mb} MiB plus "
                        f"{self.vram_reserve_mb} MiB reserve"
                    )
                cache_key, entry = victim
                evicted.append(cache_key.label())
                self._unload(cache_key, entry)
                self.gpu_runtime.empty_cache(self.device)
                snapshot = self.gpu_runtime.probe(self.device)
        return tuple(evicted)

    def _oldest_idle_entry(self) -> tuple[ModelCacheKey, _CacheEntry] | None:
        idle = [item for item in self._entries.items() if item[1].references == 0]
        return min(idle, key=lambda item: item[1].last_used) if idle else None

    def _unload(
        self,
        cache_key: ModelCacheKey,
        entry: _CacheEntry,
        *,
        suppress_errors: bool = False,
    ) -> None:
        try:
            entry.model.unload()
        except Exception:
            if not suppress_errors:
                raise
        finally:
            self._entries.pop(cache_key, None)

    def _has_vram(self, snapshot: GPUSnapshot, required_vram_mb: int) -> bool:
        if snapshot.free_memory_mb is None:
            return False
        return snapshot.free_memory_mb >= required_vram_mb + self.vram_reserve_mb

    @staticmethod
    def _required_vram_mb(descriptor: ModelDescriptor) -> int:
        value = descriptor.metadata.get("estimated_vram_mb", 0)
        if not isinstance(value, int | float) or value < 0:
            raise ValueError("estimated_vram_mb must be a non-negative number")
        return round(value)

