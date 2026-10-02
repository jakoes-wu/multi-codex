"""feature-default-switch §8：use / restore。

除第 11 条的跨文件系统用例在进程内运行（需要 mock st_dev）外，其余都以子进程运行 CLI；
中断用 MULTI_CODEX_TEST_CRASH_AT 让子进程在钩子处 os._exit(137)。
"""

import io
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import SRC, CliTestCase

sys.path.insert(0, SRC)

from multi_codex import cli, switch  # noqa: E402

CRASH = 137


class SwitchBase(CliTestCase):
    def setUp(self):
        super().setUp()
        self.link = os.path.join(self.home, ".codex")
        for name in ("a", "b"):
            self.ok("add", name)
            self.write(os.path.join(self.root, name, "auth.json"), "{}", 0o600)
            self.write(os.path.join(self.root, name, "state_5.sqlite"), name)

    def dir_of(self, name):
        return os.path.join(self.root, name)

    def point_to(self, name):
        os.symlink(self.dir_of(name), self.link)

    def target_of_link(self):
        return os.path.realpath(self.link)

    def marker(self):
        return os.path.join(self.state, "restore-journal.json")

    def hold(self, path):
        holder = subprocess.Popen([sys.executable, "-c", "import time; f = open({!r}); time.sleep(30)".format(path)])
        time.sleep(0.3)
        self.addCleanup(lambda: (holder.kill(), holder.wait()))
        return holder

    def accounts(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return sorted(json.load(handle)["accounts"])


class UseTest(SwitchBase):
    def test_show(self):
        self.assertEqual(self.ok("use").out.strip(), "(none)")
        self.point_to("a")
        self.assertEqual(self.ok("use").out.strip(), "a")
        os.unlink(self.link)
        os.symlink(self.tmp, self.link)
        self.assertIn("(unregistered:", self.ok("use").out)
        os.unlink(self.link)
        os.makedirs(self.link)
        self.assertIn("real directory", self.ok("use").out)

    def test_show_works_during_unfinished_migration(self):
        self.write(os.path.join(self.state, "migrate-journal.json"),
                   json.dumps({"name": "m", "source": "/s", "target": "/t", "backup": "/b", "mode": "rename",
                               "phase": "moved"}))
        self.assertEqual(self.run_cli("use").code, 0)

    def test_switch_and_rerun(self):
        self.point_to("a")
        result = self.ok("use", "b")
        self.assertIn("default account is now b", result.out)
        self.assertEqual(self.target_of_link(), os.path.realpath(self.dir_of("b")))
        self.assertIn("already", self.ok("use", "b").out)

    def test_create_when_missing(self):
        self.ok("use", "a")
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(self.target_of_link(), os.path.realpath(self.dir_of("a")))

    def test_busy_old_account_blocks(self):
        self.point_to("a")
        holder = self.hold(os.path.join(self.dir_of("a"), "state_5.sqlite"))
        result = self.run_cli("use", "b")
        self.assertEqual(result.code, 4, result)
        self.assertIn("pid={}".format(holder.pid), result.err)
        self.assertEqual(self.target_of_link(), os.path.realpath(self.dir_of("a")))
        result = self.run_cli("use", "b", "--skip-process-check")
        self.assertEqual(result.code, 0, result)
        self.assertIn("--skip-process-check", result.err)
        self.assertEqual(self.target_of_link(), os.path.realpath(self.dir_of("b")))

    def test_conflicts_and_errors(self):
        os.makedirs(self.link)
        before = self.snapshot()
        self.assertEqual(self.run_cli("use", "a").code, 3)
        self.assertEqual(before, self.snapshot())
        os.rmdir(self.link)
        os.symlink(self.tmp, self.link)
        self.assertEqual(self.run_cli("use", "a").code, 3)
        self.assertEqual(self.run_cli("use", "ghost").code, 1)
        os.unlink(self.link)
        real_b = os.path.join(self.tmp, "real-b")
        os.rename(self.dir_of("b"), real_b)
        os.symlink(real_b, self.dir_of("b"))
        self.assertEqual(self.run_cli("use", "b").code, 1)

    def test_dry_run(self):
        self.point_to("a")
        before = self.snapshot()
        self.assertEqual(self.run_cli("use", "b", "--dry-run").code, 0)
        self.assertEqual(before, self.snapshot())

    def test_failure_before_replace_keeps_link(self):
        self.point_to("a")
        result = self.run_cli("use", "b", env={"MULTI_CODEX_TEST_FAIL_AT": "use-replace"})
        self.assertEqual(result.code, 1, result)
        self.assertEqual(self.target_of_link(), os.path.realpath(self.dir_of("a")))
        self.assertEqual([name for name in os.listdir(self.home) if "multi-codex-tmp" in name], [])

    def test_migrate_default_after_use_is_conflict(self):
        self.point_to("a")
        self.ok("use", "b")
        self.assertEqual(self.run_cli("migrate-default", "a").code, 3)

    def test_list_shows_default(self):
        self.point_to("a")
        self.assertIn("default: a", self.ok("list").out)
        self.assertEqual(json.loads(self.ok("list", "--json").out)["default_account"], "a")
        os.unlink(self.link)
        os.makedirs(self.link)
        self.assertIsNone(json.loads(self.ok("list", "--json").out)["default_account"])


class RestoreTest(SwitchBase):
    def setUp(self):
        super().setUp()
        self.point_to("a")
        os.symlink(self.tmp, os.path.join(self.dir_of("a"), "shared-link"))
        self.original = self.tree(self.dir_of("a"))

    def tree(self, root):
        result = {}
        for path, value in self.snapshot(root).items():
            rel = os.path.relpath(path, root)
            result[rel] = value[:2] if value[0] == "file" else value
        return result

    def assert_restored(self):
        self.assertFalse(os.path.islink(self.link))
        self.assertEqual(self.original, self.tree(self.link))
        self.assertEqual(self.accounts(), ["b"])
        self.assertFalse(os.path.lexists(os.path.join(self.bin, "codex-a")))
        self.assertFalse(os.path.lexists(self.marker()))

    def test_restore(self):
        result = self.ok("restore", "a")
        self.assertIn("restored a to ~/.codex", result.out)
        self.assert_restored()
        self.assertTrue(os.path.islink(os.path.join(self.link, "shared-link")))

    def test_resume_from_each_crash_point(self):
        for point, state in (("restore-marked", "A'"), ("restore-unlinked", "B"), ("restore-renamed", "C")):
            with self.subTest(point=point):
                crashed = self.run_cli("restore", "a", env={"MULTI_CODEX_TEST_CRASH_AT": point})
                self.assertEqual(crashed.code, CRASH, crashed)
                rerun = self.ok("restore", "a")
                self.assertIn("from state {}".format(state), rerun.out)
                self.assert_restored()
                self._reset()
        self.assertEqual(self.run_cli("restore", "a").code, 0)  # 状态复位后可以再次完整 restore

    def _reset(self):
        # 把 ~/.codex 搬回账号目录、重新登记，回到 setUp 的状态。
        os.rename(self.link, self.dir_of("a"))
        self.point_to("a")
        self.ok("add", "a")

    def test_leftover_launcher_is_state_d(self):
        self.ok("restore", "a")
        launcher = os.path.join(self.bin, "codex-a")
        self.ok("add", "c")
        os.rename(os.path.join(self.bin, "codex-c"), launcher)  # 制造 config 已删、启动命令残留
        with open(launcher) as handle:
            content = handle.read().replace("account=c", "account=a")
        with open(launcher, "w") as handle:
            handle.write(content)
        result = self.ok("restore", "a")
        self.assertIn("restore of a finished", result.out)
        self.assertFalse(os.path.exists(launcher))
        self.assertEqual(self.run_cli("restore", "a").code, 1)

    def test_no_false_resume(self):
        os.unlink(self.link)
        os.makedirs(self.link)
        os.rename(self.dir_of("a"), os.path.join(self.tmp, "gone"))
        self.assertEqual(self.run_cli("restore", "a").code, 3)
        self.assertIn("a", self.accounts())
        self.assertEqual(self.run_cli("restore", "ghost").code, 1)

    def test_marker_blocks_other_write_commands(self):
        crashed = self.run_cli("restore", "a", env={"MULTI_CODEX_TEST_CRASH_AT": "restore-unlinked"})
        self.assertEqual(crashed.code, CRASH)
        for args in (("use", "b"), ("migrate-default", "x"), ("add", "y")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.code, 1, result)
                self.assertIn("restore a", result.err)
        self.assertEqual(self.run_cli("use", "b", "--dry-run").code, 0)
        checks = {c["id"]: c for c in json.loads(self.run_cli("doctor", "--json").out)["checks"]}
        self.assertEqual(checks["migration"]["status"], "fail")
        self.ok("restore", "a")
        self.assertEqual(self.run_cli("add", "y").code, 0)

    def test_inconsistent_marker(self):
        crashed = self.run_cli("restore", "a", env={"MULTI_CODEX_TEST_CRASH_AT": "restore-marked"})
        self.assertEqual(crashed.code, CRASH)
        with open(self.marker()) as handle:
            data = json.load(handle)
        data["target"] = "/elsewhere"
        self.write(self.marker(), json.dumps(data))
        self.assertEqual(self.run_cli("restore", "a").code, 3)

    def test_conflict_is_found_before_moving(self):
        # b 的启动命令被换成不受管的文件：注销时的收敛会冲突。v0.10 起在动数据之前预检（feature-auto-launcher
        # §5.1.4），所以什么都不动、不留标记。
        launcher_b = os.path.join(self.bin, "codex-b")
        os.unlink(launcher_b)
        self.write(launcher_b, "#!/bin/sh\n", 0o755)
        before = self.tree(self.dir_of("a"))
        result = self.run_cli("restore", "a")
        self.assertEqual(result.code, 3, result)
        self.assertIn("nothing was moved", result.err)
        self.assertFalse(os.path.exists(self.marker()))
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(self.tree(self.dir_of("a")), before)
        os.unlink(launcher_b)
        self.ok("restore", "a")
        self.assert_restored()

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_unregister_failure_keeps_marker(self):
        # 预检查不出的失败：启动命令目录不可写，删除 codex-a 时 OSError，第 3 步失败，保留标记。
        os.chmod(self.bin, 0o555)
        self.addCleanup(os.chmod, self.bin, 0o755)
        result = self.run_cli("restore", "a")
        self.assertEqual(result.code, 1, result)
        self.assertTrue(os.path.exists(self.marker()))
        os.chmod(self.bin, 0o755)
        self.assertIn("from state C", self.ok("restore", "a").out)
        self.assertFalse(os.path.exists(self.marker()))

    def test_refusals(self):
        holder = self.hold(os.path.join(self.dir_of("a"), "state_5.sqlite"))
        self.assertEqual(self.run_cli("restore", "a").code, 4)
        holder.kill()
        holder.wait()
        self.write(os.path.join(self.dir_of("a"), "config.toml"), 'cli_auth_credentials_store = "keyring"\n')
        self.original = self.tree(self.dir_of("a"))
        result = self.run_cli("restore", "a")
        self.assertEqual(result.code, 3, result)
        self.assertIn("`codex login`", result.err)
        self.assertNotIn("codex-a login", result.err)
        crashed = self.run_cli("restore", "a", "--accept-relogin",
                               env={"MULTI_CODEX_TEST_CRASH_AT": "restore-unlinked"})
        self.assertEqual(crashed.code, CRASH)
        rerun = self.ok("restore", "a")
        self.assertIn("log in again: codex login", rerun.out)
        self.assert_restored()

    def test_link_moved_by_use(self):
        self.ok("use", "b")
        result = self.run_cli("restore", "a")
        self.assertEqual(result.code, 3, result)
        self.assertIn("use a", result.err)

    def test_dry_run(self):
        before = self.snapshot()
        result = self.ok("restore", "a", "--dry-run")
        self.assertEqual(result.out.count("would"), 3)
        self.assertNotIn("shared-link", result.out)
        self.assertEqual(before, self.snapshot())


class CrossDeviceTest(SwitchBase):
    def test_cross_device_refused(self):
        self.point_to("a")
        env = dict(self.env)
        env.pop("PYTHONPATH")
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("MULTI_CODEX_TEST_CRASH_AT", "MULTI_CODEX_TEST_FAIL_AT", "XDG_CONFIG_HOME", "CODEX_HOME"):
            os.environ.pop(name, None)
        real_stat = os.stat
        target = self.dir_of("a")

        def fake_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if path == target:
                return os.stat_result((result.st_mode, result.st_ino, result.st_dev + 1) + tuple(result)[3:])
            return result

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(switch.os, "stat", fake_stat), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["restore", "a"])
        self.assertEqual(code, 3, err.getvalue())
        self.assertIn("different file systems", err.getvalue())
        self.assertTrue(os.path.islink(self.link))


if __name__ == "__main__":
    unittest.main()
