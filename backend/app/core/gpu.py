import importlib
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class GPUSnapshot:
    device: str
    available: bool
    name: str | None = None
    total_memory_mb: int | None = None
    free_memory_mb: int | None = None
    torch_version: str | None = None
    cuda_version: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class GPURuntime(Protocol):
    def probe(self, device: str) -> GPUSnapshot: ...

    def is_out_of_memory(self, error: BaseException) -> bool: ...

    def empty_cache(self, device: str) -> None: ...


class TorchGPURuntime:
    """Lazy PyTorch boundary used only by the inference worker."""

    def __init__(self) -> None:
        self._module: Any | None = None

    def _torch(self) -> Any:
        if self._module is None:
            self._module = importlib.import_module("torch")
        return self._module

    def probe(self, device: str) -> GPUSnapshot:
        if not device.startswith("cuda"):
            return GPUSnapshot(device=device, available=False, reason="not-a-cuda-device")
        try:
            torch = self._torch()
        except (ImportError, ModuleNotFoundError):
            return GPUSnapshot(device=device, available=False, reason="pytorch-not-installed")
        except Exception as error:
            return GPUSnapshot(
                device=device,
                available=False,
                reason=f"pytorch-import-failed:{type(error).__name__}",
            )
        try:
            if not torch.cuda.is_available():
                return GPUSnapshot(
                    device=device,
                    available=False,
                    torch_version=str(torch.__version__),
                    cuda_version=str(torch.version.cuda) if torch.version.cuda else None,
                    reason="cuda-unavailable",
                )
            index = torch.device(device).index
            if index is None:
                index = torch.cuda.current_device()
            free_bytes, total_bytes = torch.cuda.mem_get_info(index)
            properties = torch.cuda.get_device_properties(index)
            return GPUSnapshot(
                device=device,
                available=True,
                name=str(properties.name),
                total_memory_mb=total_bytes // (1024 * 1024),
                free_memory_mb=free_bytes // (1024 * 1024),
                torch_version=str(torch.__version__),
                cuda_version=str(torch.version.cuda) if torch.version.cuda else None,
            )
        except Exception as error:
            return GPUSnapshot(
                device=device,
                available=False,
                torch_version=str(getattr(torch, "__version__", "unknown")),
                reason=f"probe-failed:{type(error).__name__}",
            )

    def is_out_of_memory(self, error: BaseException) -> bool:
        class_name = type(error).__name__.lower().replace("_", "")
        if "outofmemory" in class_name:
            return True
        return "cuda out of memory" in str(error).lower()

    def empty_cache(self, device: str) -> None:
        if not device.startswith("cuda") or self._module is None:
            return
        try:
            if self._module.cuda.is_available():
                self._module.cuda.empty_cache()
        except Exception:
            return

