"""占用方式的文字说明（describe_usage）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from multi_codex.platform import describe_usage  # noqa: E402


class DescribeUsageTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(describe_usage("cwd"), "cwd")
        self.assertEqual(describe_usage("txt"), "executable")
        self.assertEqual(describe_usage("mem"), "mapped")
        self.assertEqual(describe_usage("3"), "fd 3")
        self.assertEqual(describe_usage("12u"), "fd 12")
        self.assertEqual(describe_usage("rtd"), "rtd")


if __name__ == "__main__":
    unittest.main()
