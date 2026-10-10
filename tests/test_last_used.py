"""feature-last-used §8：list 的 LAST USED 列与 list --json 的 last_used。"""

import json
import os
import sys
import time
import unittest

from helpers import SRC, CliTestCase
from test_insight import rate_limits, token_count_line

sys.path.insert(0, SRC)
from multi_codex import cli, usage  # noqa: E402
from unittest import mock  # noqa: E402

HOUR = 3600
MINUTE = 60


class AgeCellTest(unittest.TestCase):
    """T1：与 multi-claude 的 _format_age 相同的边界。"""

    def test_boundaries(self):
        now = 1_000_000_000.0
        cases = ((0, "0m ago"), (59 * MINUTE, "59m ago"), (60 * MINUTE, "1h ago"),
                 (47 * HOUR + 59 * MINUTE, "47h ago"), (48 * HOUR, "2d ago"), (-5 * MINUTE, "0m ago"))
        for age, expected in cases:
            with self.subTest(age=age):
                self.assertEqual(cli._age_cell(now - age, now), expected)
        self.assertEqual(cli._age_cell(None, now), "-")

    def test_iso(self):
        self.assertEqual(cli._iso_utc(0.9), "1970-01-01T00:00:00Z")  # 秒向下截断
        self.assertIsNone(cli._iso_utc(None))


class CandidateRaceTest(unittest.TestCase):
    """枚举之后最新的会话文件被删：退到下一个还能 stat 的文件（PR #30 评审意见）。"""

    def test_falls_back_to_next_candidate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            older = os.path.join(tmp, "older.jsonl")
            with open(older, "w") as handle:
                handle.write("x\n")
            stamp = time.time() - 3 * HOUR
            os.utime(older, (stamp, stamp))
            gone = os.path.join(tmp, "gone.jsonl")
            with mock.patch.object(usage, "_candidate_files", return_value=[gone, older]):
                self.assertAlmostEqual(usage.last_used(tmp), stamp, places=3)


class LastUsedTest(CliTestCase):
    """T2–T8。"""

    def account_dir(self, name):
        return os.path.join(self.root, name)

    def write_aged(self, path, age, content="x\n"):
        self.write(path, content)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return stamp

    def rollout(self, name, age, content="{}\n", base=None):
        base = base or os.path.join(self.account_dir(name), "sessions")
        return self.write_aged(os.path.join(base, "2026", "10", "01", "rollout-a.jsonl"), age, content)

    def history(self, name, age):
        return self.write_aged(os.path.join(self.account_dir(name), "history.jsonl"), age)

    def cell(self, name):
        """该账号行的 LAST USED 单元格：表头里 LAST USED 的起止列位置截取。"""
        lines = self.ok("list").out.splitlines()
        header = next(line for line in lines if line.startswith("NAME"))
        start, end = header.index("LAST USED"), header.index("STATUS")
        row = next(line for line in lines if line.split()[0] == name)
        return row[start:end].strip()

    def json_value(self, name):
        data = json.loads(self.ok("list", "--json").out)
        return next(item for item in data["accounts"] if item["name"] == name)["last_used"]

    def test_session_file(self):
        self.ok("add", "work")
        stamp = self.rollout("work", 3.5 * HOUR)
        self.assertEqual(self.cell("work"), "3h ago")
        self.assertEqual(self.json_value("work"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)))

    def test_history_only(self):
        self.ok("add", "work")
        self.history("work", 2.5 * 24 * HOUR)
        self.assertEqual(self.cell("work"), "2d ago")

    def test_newest_wins(self):
        self.ok("add", "work")
        self.rollout("work", 3.5 * HOUR)
        self.history("work", 10.5 * MINUTE)
        self.assertEqual(self.cell("work"), "10m ago")

    def test_archived_session(self):
        self.ok("add", "work")
        self.write_aged(os.path.join(self.account_dir("work"), "archived_sessions", "rollout-old.jsonl"), 3.5 * HOUR)
        self.assertEqual(self.cell("work"), "3h ago")

    def test_shared_history_is_ignored(self):
        self.write_aged(os.path.join(self.tmp, "shared-history.jsonl"), 0)  # 目标刚被别的账号写过
        for name in ("work", "other"):
            self.ok("add", name)
            os.symlink(os.path.join(self.tmp, "shared-history.jsonl"),
                       os.path.join(self.account_dir(name), "history.jsonl"))
        self.rollout("work", 3.5 * HOUR)
        self.assertEqual(self.cell("work"), "3h ago")
        self.assertEqual(self.cell("other"), "-")
        self.assertIsNone(self.json_value("other"))

    def link_sessions(self, name):
        target = os.path.join(self.tmp, "shared-sessions-" + name)
        os.makedirs(target)
        os.symlink(target, os.path.join(self.account_dir(name), "sessions"))
        return target

    def test_shared_sessions_without_usage(self):
        # T7a：会话文件没有额度快照，只有 LAST USED 带 *，仍要输出脚注。
        self.ok("add", "work")
        target = self.link_sessions("work")
        self.rollout("work", 10.5 * MINUTE, base=target)
        out = self.ok("list").out
        self.assertEqual(self.cell("work"), "10m ago*")
        self.assertIn("usage and last used may belong to another account", out)

    def test_shared_sessions_empty(self):
        # T7b：软链目录为空、也没有 history.jsonl：不加 *，没有脚注。
        self.ok("add", "work")
        self.link_sessions("work")
        out = self.ok("list").out
        self.assertEqual(self.cell("work"), "-")
        self.assertNotIn("sessions is shared", out)

    def test_shared_sessions_with_usage(self):
        # T7c：两列都带 *。
        self.ok("add", "work")
        target = self.link_sessions("work")
        self.rollout("work", 10.5 * MINUTE, content=token_count_line(rate_limits(used=5.0)) + "\n", base=target)
        out = self.ok("list").out
        self.assertIn(" 5h 5%* ", out)
        self.assertEqual(self.cell("work"), "10m ago*")
        self.assertIn("* sessions is shared with other accounts; usage and last used may belong", out)

    def test_nothing_and_missing_dir(self):
        self.ok("add", "fresh")
        self.ok("add", "gone")
        os.rmdir(self.account_dir("gone"))
        self.assertEqual(self.cell("fresh"), "-")
        self.assertEqual(self.cell("gone"), "-")
        self.assertIsNone(self.json_value("fresh"))
        self.assertIsNone(self.json_value("gone"))

    def test_verbose_has_no_last_used(self):
        self.ok("add", "work")
        self.rollout("work", 3.5 * HOUR)
        self.assertNotIn("LAST USED", self.ok("list", "-v").out)


if __name__ == "__main__":
    unittest.main()
