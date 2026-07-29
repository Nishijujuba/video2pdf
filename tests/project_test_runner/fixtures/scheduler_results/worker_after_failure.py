import os
from pathlib import Path
import unittest


class AfterFailureTests(unittest.TestCase):
    def test_records_execution(self):
        run_dir = Path(os.environ["VIDEO2PDF_PROJECT_TEST_RUN_DIR"])
        (run_dir / "after-failure-ran.txt").write_text("yes", encoding="utf-8")
