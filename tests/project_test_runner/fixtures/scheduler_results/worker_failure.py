import sys
import time
import unittest


class FailureTests(unittest.TestCase):
    def test_failure(self):
        time.sleep(0.03)
        print("failure-output", file=sys.stderr)
        self.fail("intentional fixture failure")
