import importlib

import pytest

from app.core.gpu import TorchGPURuntime


def test_non_cuda_probe_does_not_import_pytorch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imports: list[str] = []

    def record_import(name: str) -> None:
        imports.append(name)
        raise AssertionError("PyTorch must not be imported for CPU diagnostics")

    monkeypatch.setattr(importlib, "import_module", record_import)
    runtime = TorchGPURuntime()

    snapshot = runtime.probe("cpu")
    runtime.empty_cache("cuda:0")

    assert snapshot.available is False
    assert snapshot.reason == "not-a-cuda-device"
    assert imports == []


def test_missing_pytorch_is_reported_without_breaking_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_import(name: str) -> None:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", missing_import)

    snapshot = TorchGPURuntime().probe("cuda:0")

    assert snapshot.available is False
    assert snapshot.reason == "pytorch-not-installed"
