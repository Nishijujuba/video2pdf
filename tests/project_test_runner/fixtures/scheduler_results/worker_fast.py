import os
import sys
import unittest


class FastTests(unittest.TestCase):
    def test_output_and_environment(self):
        print("fast-stdout")
        print("fast-stderr", file=sys.stderr)
        self.assertTrue(os.environ["VIDEO2PDF_PROJECT_TEST_RUN_DIR"])
        self.assertEqual(
            os.environ["VIDEO2PDF_PROJECT_TEST_SUITE_ID"], "fixture"
        )
        self.assertTrue(os.environ["VIDEO2PDF_PROJECT_TEST_MODULE_KEY"])
        self.assertEqual(os.environ["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(os.environ["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(os.environ["TEMP"], os.environ["TMP"])
        self.assertEqual(os.environ["TMP"], os.environ["TMPDIR"])
