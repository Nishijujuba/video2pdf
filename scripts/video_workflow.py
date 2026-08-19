from __future__ import annotations

import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def _runtime_failure(command: str, message: str) -> int:
    envelope = {
        "schema_name": "workflow-result",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "command": command,
        "status": "error",
        "classification": "runtime_dependency_unavailable",
        "evidence_path": None,
        "data": {"message": message},
    }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n")
    return 70


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    try:
        from video2pdf_workflow_kernel.cli import main as kernel_main
    except (ImportError, ModuleNotFoundError, RuntimeError):
        return _runtime_failure(
            command,
            "Workflow Kernel locked runtime dependencies are unavailable",
        )
    return kernel_main()


if __name__ == "__main__":
    raise SystemExit(main())
