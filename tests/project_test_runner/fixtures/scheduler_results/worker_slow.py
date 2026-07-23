import time
import unittest


class SlowTests(unittest.TestCase):
    def test_slow(self):
        time.sleep(0.18)
