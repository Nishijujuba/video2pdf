from __future__ import annotations

import os
from pathlib import Path
import runpy


os.environ["DELIVERY_QUALITY_VARIANCE_FIXTURE"] = "1"
runpy.run_path(
    str(Path(__file__).with_name("deterministic_reviewer_adapter.py")),
    run_name="__main__",
)
