"""feature-dir-binding §8：bind / unbind，run 按目录选账号。"""

import json
import os
import sys
import tempfile
import unittest

from helpers import SRC, CliTestCase

sys.path.insert(0, SRC)

from multi_codex import binding  # noqa: E402

ECHO_HOME = ["--", "sh", "-c", 'echo "$CODEX_HOME"']


def case_insensitive(directory):
    probe = os.path.join(directory, "CaseProbe")
    os.makedirs(probe)
    try:
        return os.path.isdir(os.path.join(directory, "caseprobe"))
    finally:
        os.rmdir(probe)


class BindingTest(CliTestCase):
    def setUp(self):
        super().setUp()
        for name in ("work", "home"):
            self.ok("add", name)
        self.project = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.project, "sub", "x"))
        os.makedirs(os.path.join(self.project, "other"))

    def run_in(self, cwd, *args):
        return self.run_cli("run", *args, cwd=cwd)

    def config(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle)

    def test_v040_config_unchanged(self):
        path = os.path.join(self.state, "config.json")
        with open(path, "rb") as handle:
            before = handle.read()
        self.ok("apply")
        with open(path, "rb") as handle:
            self.assertEqual(before, handle.read())
        self.assertNotIn("bindings", self.config())

    def test_bind_list_and_run(self):
        self.ok("bind", "work", cwd=self.project)
        listing = self.ok("bind", cwd=self.project).out
        self.assertIn("* {}  work".format(binding.normalize_dir(self.project)), listing)
        self.assertEqual(list(self.config()["bindings"]), [binding.normalize_dir(self.project)])
        result = self.run_in(os.path.join(self.project, "sub"), *ECHO_HOME)
        self.assertEqual(result.code, 0, result)
        self.assertEqual(result.out.strip(), os.path.join(self.root, "work"))
        self.assertIn("using account work", result.err)

    def test_nearest_wins_and_links(self):
        self.ok("bind", "work", self.project)
        self.ok("bind", "home", os.path.join(self.project, "sub"))
        self.assertEqual(self.run_in(os.path.join(self.project, "sub", "x"), *ECHO_HOME).out.strip(),
                         os.path.join(self.root, "home"))
        self.assertEqual(self.run_in(os.path.join(self.project, "other"), *ECHO_HOME).out.strip(),
                         os.path.join(self.root, "work"))
        link = os.path.join(self.tmp, "link")
        os.symlink(self.project, link)
        self.assertEqual(self.run_in(link, *ECHO_HOME).out.strip(), os.path.join(self.root, "work"))

    def test_no_binding_and_dangling(self):
        self.assertEqual(self.run_in(self.project, "--", "true").code, 1)
        self.assertEqual(self.run_in(self.project, "work", "--", "true").code, 0)
        self.ok("bind", "work", self.project)
        data = self.config()
        del data["accounts"]["work"]
        self.write(os.path.join(self.state, "config.json"), json.dumps(data))
        self.assertEqual(self.run_in(self.project, "--", "true").code, 1)
        checks = {c["id"]: c for c in json.loads(self.run_cli("doctor", "--json").out)["checks"]}
        self.assertEqual(checks["bindings"]["status"], "warn")

    def test_unbind(self):
        self.ok("bind", "work", self.project)
        self.ok("unbind", self.project)
        self.assertEqual(self.run_in(self.project, "--", "true").code, 1)
        self.assertIn("not bound", self.ok("unbind", self.project).out)
        gone = os.path.join(self.tmp, "gone")
        os.makedirs(gone)
        self.ok("bind", "work", gone)
        os.rmdir(gone)
        self.ok("unbind", gone)
        self.assertNotIn("bindings", self.config())

    def test_unbind_in_subdirectory_mentions_parent(self):
        self.ok("bind", "work", self.project)
        result = self.ok("unbind", cwd=os.path.join(self.project, "sub"))
        self.assertIn("not bound", result.out)
        self.assertIn("effective binding is {}".format(binding.normalize_dir(self.project)), result.out)

    def test_unbind_without_config_creates_nothing(self):
        os.unlink(os.path.join(self.state, "config.json"))
        self.assertIn("not bound", self.ok("unbind", self.project).out)
        self.assertFalse(os.path.exists(os.path.join(self.state, "config.json")))

    def test_bind_errors(self):
        before = self.snapshot()
        self.assertEqual(self.run_cli("bind", "ghost", self.project).code, 1)
        self.assertEqual(self.run_cli("bind", "work", "/no/such/dir").code, 1)
        self.assertEqual(before, self.snapshot())

    def test_unregistering_drops_bindings(self):
        for command in (("remove", "work"), ("apply", "-f", "FILE")):
            with self.subTest(command=command[0]):
                self.ok("add", "work")
                self.ok("bind", "work", self.project)
                if command[0] == "apply":
                    spec = self.write(os.path.join(self.tmp, "spec.json"),
                                      json.dumps({"version": 1, "accounts": {"home": {}}}))
                    command = ("apply", "-f", spec)
                self.ok(*command)
                self.assertNotIn("bindings", self.config())

    def test_restore_drops_bindings(self):
        os.symlink(os.path.join(self.root, "work"), os.path.join(self.home, ".codex"))
        self.ok("bind", "work", self.project)
        self.ok("restore", "work")
        self.assertNotIn("bindings", self.config())

    @unittest.skipUnless(os.path.islink("/tmp"), "/tmp is not a link on this system")
    def test_unbind_deleted_dir_through_tmp_link(self):
        # 绑定时存的是 /private/tmp/...；目录删掉后再用 /tmp/... 解除，不能因为 abspath 匹配不上。
        target = tempfile.mkdtemp(prefix="mcx-bind-", dir="/tmp")
        self.addCleanup(lambda: os.path.isdir(target) and os.rmdir(target))
        self.ok("bind", "work", target)
        os.rmdir(target)
        self.ok("unbind", target)
        self.assertNotIn("bindings", self.config())

    def test_apply_file_keeps_bindings(self):
        self.ok("bind", "work", self.project)
        spec = self.write(os.path.join(self.tmp, "spec.json"),
                          json.dumps({"version": 1, "accounts": {"work": {}, "home": {}}}))
        self.ok("apply", "-f", spec)
        self.assertEqual(list(self.config()["bindings"].values()), ["work"])

    def test_dry_run(self):
        self.ok("bind", "home", os.path.join(self.project, "sub"))
        before = self.snapshot()
        self.assertIn("(dry-run)", self.ok("bind", "work", self.project, "--dry-run").out)
        self.ok("unbind", os.path.join(self.project, "sub"), "--dry-run")
        self.assertEqual(before, self.snapshot())

    def test_case_insensitive_binding(self):
        if not case_insensitive(self.tmp):
            self.skipTest("the file system is case-sensitive")
        real = os.path.join(self.tmp, "CaseDir")
        os.makedirs(real)
        self.ok("bind", "work", os.path.join(self.tmp, "casedir"))
        self.assertEqual(self.run_in(real, *ECHO_HOME).out.strip(), os.path.join(self.root, "work"))

    def test_case_sensitive_siblings(self):
        if case_insensitive(self.tmp):
            self.skipTest("the file system is case-insensitive")
        os.makedirs(os.path.join(self.tmp, "Foo"))
        os.makedirs(os.path.join(self.tmp, "foo"))
        self.ok("bind", "work", os.path.join(self.tmp, "foo"))
        self.assertEqual(list(self.config()["bindings"]), [os.path.join(self.tmp, "foo")])
        self.assertEqual(self.run_in(os.path.join(self.tmp, "foo"), *ECHO_HOME).code, 0)
        self.assertEqual(self.run_in(os.path.join(self.tmp, "Foo"), "--", "true").code, 1)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "permissions do not apply to root")
    def test_normalize_survives_unlistable_parent(self):
        locked = os.path.join(self.tmp, "locked")
        os.makedirs(os.path.join(locked, "inner"))
        os.chmod(locked, 0o111)
        try:
            self.assertEqual(binding.normalize_dir(os.path.join(locked, "inner")),
                             os.path.join(os.path.realpath(locked), "inner"))
        finally:
            os.chmod(locked, 0o755)

    def test_conflict_reports_binding_not_changed(self):
        launcher = os.path.join(self.bin, "codex-home")
        os.unlink(launcher)
        self.write(launcher, "#!/bin/sh\n", 0o755)
        result = self.run_cli("bind", "work", self.project)
        self.assertEqual(result.code, 3, result)
        self.assertIn("binding not changed", result.out)
        self.assertNotIn("bindings", self.config())

    def test_bindings_survive_use_and_completion(self):
        self.ok("bind", "work", self.project)
        self.ok("use", "home")
        self.assertIn("bindings", self.config())
        self.assertIn("bind", self.ok("completion", "bash").out)


if __name__ == "__main__":
    unittest.main()
