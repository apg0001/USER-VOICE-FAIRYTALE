import atexit
from threading import Lock

from app.core.config import Settings
from app.core.gpu import TorchGPURuntime
from app.models.manager import ModelManager

_manager: ModelManager | None = None
_signature: tuple[str, int, int] | None = None
_manager_lock = Lock()


def get_worker_model_manager(settings: Settings) -> ModelManager:
    global _manager, _signature
    signature = (
        settings.cuda_device,
        settings.model_cache_limit,
        settings.gpu_vram_reserve_mb,
    )
    with _manager_lock:
        if _manager is None or _signature != signature:
            if _manager is not None:
                _manager.shutdown()
            _manager = ModelManager(
                device=settings.cuda_device,
                max_cached_models=settings.model_cache_limit,
                vram_reserve_mb=settings.gpu_vram_reserve_mb,
                gpu_runtime=TorchGPURuntime(),
            )
            _signature = signature
        return _manager


def reset_worker_model_manager() -> None:
    global _manager, _signature
    with _manager_lock:
        if _manager is not None:
            _manager.shutdown()
        _manager = None
        _signature = None


atexit.register(reset_worker_model_manager)
