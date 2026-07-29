import os
from pathlib import Path
import subprocess
import sys
import unittest


class AlphaTests(unittest.TestCase):
    def test_output_and_external_run_environment(self):
        print("fixture-alpha-stdout")
        print("fixture-alpha-stderr", file=sys.stderr)
        self.assertTrue(os.environ["VIDEO2PDF_PROJECT_TEST_RUN_DIR"])
        execution_root = Path(__file__).resolve().parents[2]
        self.assertIn("execution-source-files", execution_root.parts)
        self.assertEqual(
            (
                execution_root
                / "requirements"
                / "video-workflow-runtime.in"
            ).read_text(encoding="utf-8"),
            "jsonschema==4.26.0\n",
        )
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=execution_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(Path(completed.stdout.strip()).resolve(), execution_root)

        nested_repo = Path(os.environ["TEMP"]) / "nested-repo"
        nested_repo.mkdir()
        initialized = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=nested_repo,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        nested_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=nested_repo,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(nested_root.returncode, 0, nested_root.stderr)
        self.assertEqual(
            Path(nested_root.stdout.strip()).resolve(),
            nested_repo.resolve(),
        )
