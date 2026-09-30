import json

from app.core.config import get_settings
from app.core.gpu import TorchGPURuntime


def main() -> int:
    settings = get_settings()
    snapshot = TorchGPURuntime().probe(settings.cuda_device)
    print(json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if snapshot.available else 1


if __name__ == "__main__":
    raise SystemExit(main())
