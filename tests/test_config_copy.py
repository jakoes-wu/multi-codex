"""feature-config-copy §8：add --config-from。"""

import json
import os
import stat
import unittest

from helpers import CliTestCase

CONTENT = 'model = "gpt-x"\n[projects."/p"]\ntrust_level = "trusted"\n'


class ConfigCopyTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.ok("add", "work")
        self.source = self.write(os.path.join(self.root, "work", "config.toml"), CONTENT)
        self.target = os.path.join(self.root, "new", "config.toml")

    def read(self, path):
        with open(path) as handle:
            return handle.read()

    def config_json(self):
        with open(os.path.join(self.state, "config.json"), "rb") as handle:
            return handle.read()

    def test_copy_new_account_and_rerun(self):
        result = self.ok("add", "new", "--config-from", "work")
        self.assertIn("create config-file", result.out)
        with open(self.target) as handle:
            self.assertEqual(handle.read(), CONTENT)
        self.assertEqual(stat.S_IMODE(os.stat(self.target).st_mode), 0o600)
        self.assertIn("unchanged config-file", self.ok("add", "new", "--config-from", "work").out)

    def test_existing_different_content_is_conflict(self):
        self.ok("add", "new")
        self.write(self.target, "other\n")
        before = (self.config_json(), self.read(self.target))
        result = self.run_cli("add", "new", "--config-from", "work", "--proxy", "7901")
        self.assertEqual(result.code, 3, result)
        self.assertEqual(before, (self.config_json(), self.read(self.target)))

    def test_conflict_on_new_account_registers_nothing(self):
        os.makedirs(self.target)  # 目录占住了 config.toml 的位置
        result = self.run_cli("add", "new", "--config-from", "work")
        self.assertEqual(result.code, 3, result)
        self.assertNotIn("new", json.loads(self.config_json())["accounts"])

    def test_source_is_a_link(self):
        shared = self.write(os.path.join(self.tmp, "shared-config.toml"), CONTENT)
        os.unlink(self.source)
        os.symlink(shared, self.source)
        self.ok("add", "new", "--config-from", "work")
        self.assertFalse(os.path.islink(self.target))
        with open(self.target) as handle:
            self.assertEqual(handle.read(), CONTENT)

    def test_errors(self):
        self.assertEqual(self.run_cli("add", "new", "--config-from", "ghost").code, 1)
        self.assertEqual(self.run_cli("add", "work", "--config-from", "work").code, 2)
        self.ok("add", "empty")
        self.assertEqual(self.run_cli("add", "new", "--config-from", "empty").code, 1)
        with open(self.source, "wb") as handle:
            handle.write(b"model = \"\xff\xfe\"\n")
        self.assertEqual(self.run_cli("add", "new", "--config-from", "work").code, 1)
        self.assertNotIn("new", json.loads(self.config_json())["accounts"])

    def test_shared_config_is_conflict(self):
        shared = os.path.join(self.tmp, "shared")
        self.write(os.path.join(shared, "config.toml"), CONTENT)
        self.ok("init", "--shared-dir", shared, "--shared-items", "config.toml")
        result = self.run_cli("add", "new", "--shared", "--config-from", "work")
        self.assertEqual(result.code, 3, result)
        self.assertIn("does not need copying", result.err)
        self.assertNotIn("new", json.loads(self.config_json())["accounts"])

    def test_dry_run_and_doctor(self):
        before = self.snapshot()
        self.assertEqual(self.run_cli("add", "new", "--config-from", "work", "--dry-run").code, 0)
        self.assertEqual(before, self.snapshot())
        self.ok("add", "new", "--config-from", "work")
        checks = {c["id"]: c for c in json.loads(self.run_cli("doctor", "--json").out)["checks"]}
        self.assertEqual(checks["drift"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
