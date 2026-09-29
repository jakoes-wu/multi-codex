"""方案 §8 第 4（迁移相关）、6、7、8、9、10、11 条：默认目录迁移。

中断用 MULTI_CODEX_TEST_CRASH_AT 让子进程在注入点 os._exit(137)，模拟进程被杀；
回滚用 MULTI_CODEX_TEST_FAIL_AT 抛出可捕获异常。两者可以同时设置。
需要在同一次运行中改动文件系统的场景（S 被重建、EXDEV、校验失败）改为进程内调用并替换函数。
"""

import errno
import io
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import SRC, CliTestCase

sys.path.insert(0, SRC)

from multi_codex import migrate  # noqa: E402

CRASH = 137


class MigrateBase(CliTestCase):
    def setUp(self):
        super().setUp()
        self.source = os.path.join(self.home, ".codex")
        self.target = os.path.join(self.root, "main")
        self.write(os.path.join(self.source, "auth.json"), "{}", 0o600)
        self.write(os.path.join(self.source, "state_5.sqlite"), "db" * 1000)
        self.write(os.path.join(self.source, "sessions", "2026", "a.jsonl"), "line\n")
        os.symlink("sessions", os.path.join(self.source, "sessions-link"))
        self.original = self._tree(self.source)

    def _tree(self, root):
        """以相对路径表示的目录树内容，用于比较迁移前后数据是否一致。"""
        snap = self.snapshot(root)
        result = {}
        for path, value in snap.items():
            rel = os.path.relpath(path, root)
            if value[0] == "file":
                value = ("file", value[1], value[3])  # 去掉 mtime：copy2 会保留，但 rename 前后都一致
            result[rel] = value
        return result

    def backups(self):
        return [name for name in os.listdir(self.home) if ".multi-codex-bak." in name]

    def assert_migrated(self):
        self.assertTrue(os.path.islink(self.source), "source should be a link")
        self.assertEqual(os.path.realpath(self.source), os.path.realpath(self.target))
        self.assertEqual(self.original, self._tree(self.target))
        self.assertIsNone(self.journal())
        self.assertTrue(os.path.exists(os.path.join(self.bin, "codex-main")))
        with open(os.path.join(self.state, "config.json")) as handle:
            self.assertIn("main", json.load(handle)["accounts"])

    def migrate(self, *extra, env=None):
        return self.run_cli("migrate-default", "main", *extra, env=env)


class RenameMigrationTest(MigrateBase):
    def test_rename_and_rerun(self):
        result = self.migrate()
        self.assertEqual(result.code, 0, result)
        self.assert_migrated()
        self.assertEqual(os.stat(os.path.join(self.target, "auth.json")).st_mode & 0o777, 0o600)
        again = self.migrate()
        self.assertEqual(again.code, 0, again)
        self.assertIn("already migrated", again.out)

    def test_source_link_without_registration(self):
        os.makedirs(self.root)
        os.rename(self.source, self.target)
        os.symlink(self.target, self.source)
        result = self.migrate()
        self.assertEqual(result.code, 0, result)
        self.assert_migrated()

    def test_conflicts_before_moving(self):
        cases = []
        os.makedirs(self.target)
        cases.append("target exists")
        before = self.snapshot()
        self.assertEqual(self.migrate().code, 3)
        self.assertEqual(before, self.snapshot())
        os.rmdir(self.target)
        os.symlink("/nonexistent", self.target)  # 断开的软链也算存在
        self.assertEqual(self.migrate().code, 3)
        os.unlink(self.target)
        self.write(os.path.join(self.bin, "codex-main"), "#!/bin/sh\n", 0o755)
        before = self.snapshot()
        result = self.migrate()
        self.assertEqual(result.code, 3, result)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(os.path.isdir(self.source) and not os.path.islink(self.source))

    def test_case_insensitive_registered_name(self):
        self.ok("add", "Main")
        self.assertEqual(self.migrate().code, 3)

    def test_codex_home_warning(self):
        result = self.migrate(env={"CODEX_HOME": os.path.join(self.tmp, "elsewhere"),
                                   "CODEX_API_KEY": "x"})
        self.assertEqual(result.code, 0, result)
        self.assertIn("CODEX_HOME is set", result.err)
        self.assertIn("CODEX_API_KEY", result.err)
        self.assert_migrated()


class CopyMigrationTest(MigrateBase):
    def test_copy_skips_special_files_and_removes_backup(self):
        cwd = os.getcwd()
        os.chdir(self.source)
        try:
            sock = socket.socket(socket.AF_UNIX)
            sock.bind("ipc.sock")
        finally:
            os.chdir(cwd)
        os.mkfifo(os.path.join(self.source, "fifo"))
        self.original = self._tree(self.source)
        del self.original["ipc.sock"], self.original["fifo"]
        try:
            result = self.migrate("--copy")
        finally:
            sock.close()
        self.assertEqual(result.code, 0, result)
        self.assertIn("skipped special file", result.out)
        self.assert_migrated()
        self.assertEqual(self.backups(), [])

    def test_unreadable_subdirectory_aborts_copy(self):
        locked = os.path.join(self.source, "locked")
        self.write(os.path.join(locked, "data.txt"), "data")
        os.chmod(locked, 0)
        self.addCleanup(lambda: os.path.isdir(locked) and os.chmod(locked, 0o755))
        result = self.migrate("--copy", "--skip-process-check")
        os.chmod(locked, 0o755)
        self.assertEqual(result.code, 1, result)
        self.assertTrue(os.path.isdir(self.source) and not os.path.islink(self.source))
        with open(os.path.join(locked, "data.txt")) as handle:
            self.assertEqual(handle.read(), "data")
        self.assertFalse(os.path.lexists(self.target))
        self.assertIsNone(self.journal())
        self.assertEqual(self.backups(), [])

    def test_resume_reverify_detects_changed_source(self):
        """续跑表第 8 行的失败分支：复制后 S 又变了，重新校验不通过，删掉 T 重新复制。"""
        self.assertEqual(self.migrate("--copy", env={"MULTI_CODEX_TEST_CRASH_AT": "journal-moved"}).code, CRASH)
        self.write(os.path.join(self.source, "state_5.sqlite"), "changed after copy")
        self.original = self._tree(self.source)
        result = self.migrate()
        self.assertEqual(result.code, 0, result)
        self.assertIn("copying again", result.out)
        self.assert_migrated()
        self.assertEqual(self.backups(), [])

    def test_keep_backup(self):
        result = self.migrate("--copy", "--keep-backup")
        self.assertEqual(result.code, 0, result)
        self.assert_migrated()
        self.assertEqual(len(self.backups()), 1)

    def test_rollback_after_park(self):
        result = self.migrate("--copy", env={"MULTI_CODEX_TEST_FAIL_AT": "journal-parked"})
        self.assertEqual(result.code, 1, result)
        self.assertTrue(os.path.isdir(self.source) and not os.path.islink(self.source))
        self.assertEqual(self.original, self._tree(self.source))
        self.assertFalse(os.path.exists(self.target))
        self.assertIsNone(self.journal())
        self.assertEqual(self.migrate("--copy").code, 0)
        self.assert_migrated()

    def test_rollback_interrupted_at_each_step(self):
        for point in ("journal-rolled-back", "rollback-restore", "rollback-remove-target"):
            with self.subTest(point=point):
                result = self.migrate("--copy", env={"MULTI_CODEX_TEST_FAIL_AT": "journal-parked",
                                                     "MULTI_CODEX_TEST_CRASH_AT": point})
                self.assertEqual(result.code, CRASH, result)
                self.assertIsNotNone(self.journal())
                rerun = self.migrate("--copy")
                self.assertEqual(rerun.code, 0, rerun)
                self.assert_migrated()
                self._undo()

    def _undo(self):
        """把已迁移的状态还原成初始状态，供下一个子用例使用。"""
        os.unlink(self.source)
        os.rename(self.target, self.source)
        os.unlink(os.path.join(self.bin, "codex-main"))
        os.unlink(os.path.join(self.state, "config.json"))

    def test_source_busy_before_park(self):
        # 复制完成后、park 前被占用：第一次在 copy-done 处崩溃模拟，然后占用 S 再续跑。
        self.assertEqual(self.migrate("--copy", env={"MULTI_CODEX_TEST_CRASH_AT": "journal-moved"}).code, CRASH)
        holder = subprocess.Popen(["sleep", "30"], cwd=self.source)
        try:
            time.sleep(0.2)
            result = self.migrate()
            self.assertEqual(result.code, 4, result)
            self.assertIn('"phase": "moved"', self.journal())
        finally:
            holder.kill()
            holder.wait()
        result = self.migrate()
        self.assertEqual(result.code, 0, result)
        self.assert_migrated()


class InterruptionTest(MigrateBase):
    RENAME_POINTS = ["journal-planned", "rename-target", "journal-moved", "link", "journal-linked",
                     "register", "journal-registered"]
    COPY_POINTS = ["journal-planned", "journal-copying", "copy-midway", "copy-done", "journal-moved",
                   "rename-backup", "journal-parked", "link", "journal-linked", "register",
                   "journal-registered", "remove-backup"]

    def _check_points(self, points, extra):
        for point in points:
            with self.subTest(point=point):
                result = self.migrate(*extra, env={"MULTI_CODEX_TEST_CRASH_AT": point})
                self.assertEqual(result.code, CRASH, result)
                rerun = self.migrate(*extra)
                self.assertEqual(rerun.code, 0, rerun)
                self.assert_migrated()
                self.assertEqual(self.backups(), [])
                os.unlink(self.source)
                os.rename(self.target, self.source)
                os.unlink(os.path.join(self.bin, "codex-main"))
                os.unlink(os.path.join(self.state, "config.json"))

    def test_rename_mode(self):
        self._check_points(self.RENAME_POINTS, [])

    def test_copy_mode(self):
        self._check_points(self.COPY_POINTS, ["--copy"])

    def test_rebuilt_source_after_park_row4(self):
        self.assertEqual(self.migrate("--copy", env={"MULTI_CODEX_TEST_CRASH_AT": "rename-backup"}).code, CRASH)
        os.makedirs(self.source)
        before = self.snapshot()
        result = self.migrate()
        self.assertEqual(result.code, 1, result)
        self.assertIn("was recreated", result.err)
        self.assertEqual(before, self.snapshot())
        os.rmdir(self.source)
        self.assertEqual(self.migrate().code, 0)
        self.assert_migrated()

    def test_rebuilt_source_after_rename_row9(self):
        self.assertEqual(self.migrate(env={"MULTI_CODEX_TEST_CRASH_AT": "journal-moved"}).code, CRASH)
        os.makedirs(self.source)
        result = self.migrate()
        self.assertEqual(result.code, 1, result)
        self.assertIn("to abandon this migration", result.err)
        os.rmdir(self.source)
        self.assertEqual(self.migrate().code, 0)
        self.assert_migrated()

    def test_unknown_state_row10(self):
        self.assertEqual(self.migrate(env={"MULTI_CODEX_TEST_CRASH_AT": "journal-moved"}).code, CRASH)
        os.rename(self.target, os.path.join(self.tmp, "moved-away"))
        before = self.snapshot()
        result = self.migrate()
        self.assertEqual(result.code, 1, result)
        self.assertIn("cannot determine", result.err)
        self.assertEqual(before, self.snapshot())

    def test_resume_uses_recorded_options(self):
        result = self.migrate("--copy", "--keep-backup", env={"MULTI_CODEX_TEST_CRASH_AT": "journal-parked"})
        self.assertEqual(result.code, CRASH)
        rerun = self.migrate()
        self.assertEqual(rerun.code, 0, rerun)
        self.assertIn("options differ", rerun.err)
        self.assertEqual(len(self.backups()), 1)

    def test_resume_with_other_name(self):
        self.assertEqual(self.migrate(env={"MULTI_CODEX_TEST_CRASH_AT": "journal-moved"}).code, CRASH)
        self.assertEqual(self.run_cli("migrate-default", "other").code, 2)


class BusyCheckTest(MigrateBase):
    def _assert_busy(self, holder):
        try:
            time.sleep(0.3)
            before = self.snapshot()
            result = self.migrate()
            self.assertEqual(result.code, 4, result)
            self.assertIn("pid={}".format(holder.pid), result.err)
            self.assertEqual(before, self.snapshot())
        finally:
            holder.kill()
            holder.wait()

    def test_open_file(self):
        code = "import time; f = open({!r}); time.sleep(30)".format(os.path.join(self.source, "state_5.sqlite"))
        self._assert_busy(subprocess.Popen([sys.executable, "-c", code]))

    def test_working_directory(self):
        self._assert_busy(subprocess.Popen(["sleep", "30"], cwd=self.source))

    def test_same_prefix_is_not_busy(self):
        sibling = os.path.join(self.home, ".codex-shared")
        os.makedirs(sibling)
        holder = subprocess.Popen(["sleep", "30"], cwd=sibling)
        try:
            time.sleep(0.3)
            self.assertEqual(self.migrate().code, 0)
        finally:
            holder.kill()
            holder.wait()

    def test_check_failure_and_skip(self):
        fake_lsof = self.write(os.path.join(self.fakebin, "lsof"), "#!/bin/sh\necho broken >&2\nexit 1\n", 0o755)
        self.assertTrue(os.path.exists(fake_lsof))
        result = self.migrate()
        self.assertEqual(result.code, 1, result)
        self.assertIn("--skip-process-check", result.err)
        self.assertEqual(self.migrate("--skip-process-check").code, 0)
        self.assert_migrated()

    @unittest.skipUnless(sys.platform.startswith("linux"), "the /proc fallback exists only on Linux")
    def test_proc_fallback_without_lsof(self):
        env = {"PATH": self.fakebin + ":/nonexistent"}
        holder = subprocess.Popen(["sleep", "30"], cwd=self.source)
        try:
            time.sleep(0.3)
            result = self.migrate(env=env)
            self.assertEqual(result.code, 4, result)
        finally:
            holder.kill()
            holder.wait()


class InProcessTest(MigrateBase):
    """需要在同一次运行中改动文件系统的场景。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, self.env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("MULTI_CODEX_TEST_CRASH_AT", "MULTI_CODEX_TEST_FAIL_AT", "XDG_CONFIG_HOME", "CODEX_HOME"):
            os.environ.pop(name, None)

    def call(self, copy=False):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = migrate.migrate_default("main", None, copy, False, None, True, False)
        return code, out.getvalue(), err.getvalue()

    def test_source_rebuilt_before_link(self):
        real_hook = migrate._test_hook

        def hook(point):
            if point == "journal-moved":
                os.makedirs(self.source)
            real_hook(point)

        with mock.patch.object(migrate, "_test_hook", hook):
            code, _, err = self.call()
        self.assertEqual(code, 1, err)
        self.assertIn("was recreated", err)
        self.assertIn('"phase": "moved"', self.journal())
        self.assertEqual(self.original, self._tree(self.target))
        os.rmdir(self.source)
        code, out, err = self.call()
        self.assertEqual(code, 0, err)
        self.assert_migrated()

    def test_exdev_switches_to_copy_and_resumes(self):
        real_rename = os.rename

        def rename(src, dst):
            if src == self.source and dst == self.target:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_rename(src, dst)

        class Killed(BaseException):
            pass

        real_hook = migrate._test_hook

        def hook(point):
            if point == "copy-midway":
                raise Killed()
            real_hook(point)

        with mock.patch.object(migrate.os, "rename", rename), mock.patch.object(migrate, "_test_hook", hook):
            with self.assertRaises(Killed):
                self.call()
        journal = json.loads(self.journal())
        self.assertEqual(journal["mode"], "copy")
        self.assertEqual(journal["phase"], "copying")
        code, _, err = self.call()
        self.assertEqual(code, 0, err)
        self.assert_migrated()

    def test_backup_cleanup_failure_only_warns(self):
        real_remove = migrate.remove_path

        def remove(path):
            if ".multi-codex-bak." in path:
                raise TypeError("simulated rmtree failure")
            return real_remove(path)

        with mock.patch.object(migrate, "remove_path", remove):
            code, _, err = self.call(copy=True)
        self.assertEqual(code, 0, err)
        self.assertIn("could not remove backup", err)
        self.assert_migrated()

    def test_rollback_failure_keeps_journal(self):
        """回滚中删除 T 失败（可捕获异常）：事务记录停在 rolled-back，重跑先完成回滚再迁移。"""
        real_remove = migrate.remove_path

        def remove(path):
            if path == self.target:
                raise OSError("simulated failure removing the copy")
            return real_remove(path)

        with mock.patch.dict(os.environ, {"MULTI_CODEX_TEST_FAIL_AT": "journal-parked"}), \
                mock.patch.object(migrate, "remove_path", remove):
            code, _, err = self.call(copy=True)
        self.assertEqual(code, 1, err)
        self.assertIn("rollback failed", err)
        self.assertIn('"phase": "rolled-back"', self.journal())
        self.assertEqual(self.original, self._tree(self.source))
        code, _, err = self.call(copy=True)
        self.assertEqual(code, 0, err)
        self.assert_migrated()

    def test_verification_failure(self):
        with mock.patch.object(migrate, "diff_manifests", lambda a, b: ["x"]):
            code, _, err = self.call(copy=True)
        self.assertEqual(code, 1, err)
        self.assertIn("verification failed", err)
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(self.original, self._tree(self.source))
        self.assertIsNone(self.journal())


if __name__ == "__main__":
    unittest.main()
