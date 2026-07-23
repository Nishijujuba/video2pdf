import os
import sys
import unittest


class AlphaTests(unittest.TestCase):
    def test_output_and_external_run_environment(self):
        print("fixture-alpha-stdout")
        print("fixture-alpha-stderr", file=sys.stderr)
        self.assertTrue(os.environ["VIDEO2PDF_PROJECT_TEST_RUN_DIR"])
