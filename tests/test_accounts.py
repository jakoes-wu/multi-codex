"""方案 §8 第 1、2、4、12、13、14、15 条：幂等、启动命令、冲突、共享资源、纳管、apply/remove、锁。"""

import json
import os
import shutil
import signal
import subprocess
import sys
import unittest

from helpers import SRC, CliTestCase


class IdempotencyTest(CliTestCase):
    def test_every_write_command_is_idempotent(self):
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(os.path.join(shared, "skills"))
        apply_file = self.write(os.path.join(self.tmp, "apply.json"), json.dumps({
            "version": 1, "root": "~/.cx", "bin_dir": "~/.local/bin",
            "shared": {"dir": shared, "items": ["skills"]},
            "accounts": {"a": {"proxy": "7901", "shared": True}, "b": {}}}))
        commands = [
            ("init", "--shared-dir", shared),
            ("add", "a", "--proxy", "7901", "--shared"),
            ("proxy", "a", "socks5://127.0.0.1:1080"),
            ("apply", "-f", apply_file),
            ("remove", "b"),
        ]
        for command in commands:
            with self.subTest(command=command):
                self.ok(*command)
                before = self.snapshot()
                second = self.ok(*command)
                for word in ("create", "update", "delete"):
                    self.assertNotIn("] " + word, second.out, second)
                self.assertEqual(before, self.snapshot())


class LauncherTest(CliTestCase):
    def test_arguments_codex_home_and_proxy(self):
        self.ok("add", "work", "--proxy", "7901")
        code, args, env, _ = self.run_launcher("work", "--x", "a b", env={"NO_PROXY": "10.0.0.0/8"})
        self.assertEqual(code, 0)
        self.assertEqual(args, ["--x", "a b"])
        self.assertEqual(env["CODEX_HOME"], os.path.join(self.root, "work"))
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            self.assertEqual(env[name], "http://127.0.0.1:7901")
        self.assertNotIn("ALL_PROXY", env)
        # NO_PROXY 在原值后追加；no_proxy 原来为空，不能留前导逗号。
        self.assertEqual(env["NO_PROXY"], "10.0.0.0/8,localhost,127.0.0.1,::1")
        self.assertEqual(env["no_proxy"], "localhost,127.0.0.1,::1")

    def test_off_and_inherit_with_proxy_in_parent_environment(self):
        parent = {name: "http://parent:1" for name in
                  ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
                   "https_proxy", "http_proxy", "all_proxy", "no_proxy")}
        self.ok("add", "work", "--proxy", "off")
        _, _, env, _ = self.run_launcher("work", env=parent)
        self.assertFalse(set(parent) & set(env), env)
        self.ok("proxy", "work", "inherit")
        _, _, env, _ = self.run_launcher("work", env=parent)
        for name, value in parent.items():
            self.assertEqual(env[name], value)

    def test_http_proxy_clears_inherited_all_proxy(self):
        self.ok("add", "work", "--proxy", "7901")
        _, _, env, _ = self.run_launcher("work", env={"ALL_PROXY": "socks5://parent:1",
                                                      "all_proxy": "socks5://parent:1"})
        self.assertNotIn("ALL_PROXY", env)
        self.assertNotIn("all_proxy", env)

    def test_socks_proxy_warns_without_changing_exit_code(self):
        self.assertNotIn("SOCKS proxy", self.ok("add", "a", "--proxy", "7901").err)
        result = self.ok("proxy", "a", "socks5h://127.0.0.1:1080")
        self.assertIn("SOCKS proxy", result.err)
        self.assertIn("mixed", result.err)
        path = self.write(os.path.join(self.tmp, "c.json"), json.dumps(
            {"version": 1, "accounts": {"a": {"proxy": "socks5://127.0.0.1:1080"}, "b": {"proxy": "7901"}}}))
        result = self.ok("apply", "-f", path)
        self.assertIn("SOCKS proxy for account 'a'", result.err)
        self.assertNotIn("account 'b'", result.err)

    def test_socks_sets_all_proxy(self):
        self.ok("add", "work", "--proxy", "socks5h://127.0.0.1:1080")
        _, _, env, _ = self.run_launcher("work")
        self.assertEqual(env["ALL_PROXY"], "socks5h://127.0.0.1:1080")
        self.assertEqual(env["all_proxy"], "socks5h://127.0.0.1:1080")

    def test_missing_account_dir(self):
        self.ok("add", "work")
        shutil.rmtree(os.path.join(self.root, "work"))
        code, args, _, stderr = self.run_launcher("work")
        self.assertEqual(code, 1)
        self.assertEqual(args, [])
        self.assertIn("account directory does not exist", stderr)
        self.assertIn("missing-dir", self.ok("list").out)

    def test_missing_dir_message_keeps_backslash(self):
        root = os.path.join(self.tmp, "back\\slash")
        self.ok("init", "--root", root)
        self.ok("add", "work")
        shutil.rmtree(os.path.join(root, "work"))
        _, _, _, stderr = self.run_launcher("work")
        self.assertIn(os.path.join(root, "work"), stderr)

    def test_root_with_space_and_quote(self):
        root = os.path.join(self.tmp, "my root's dir")
        self.ok("init", "--root", root)
        self.ok("add", "work")
        code, _, env, _ = self.run_launcher("work")
        self.assertEqual(code, 0)
        self.assertEqual(env["CODEX_HOME"], os.path.join(root, "work"))

    def test_codex_not_on_path(self):
        self.ok("add", "work")
        env = {"PATH": "/usr/bin:/bin"}
        code, _, _, stderr = self.run_launcher("work", env=env)
        self.assertEqual(code, 127)
        self.assertIn("codex not found", stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck is not installed")
    def test_shellcheck_generated_launchers(self):
        self.ok("add", "a", "--proxy", "7901")
        self.ok("add", "b", "--proxy", "off")
        self.ok("add", "c", "--proxy", "socks5://127.0.0.1:1080")
        self.ok("add", "d")
        for name in "abcd":
            proc = subprocess.run(["shellcheck", os.path.join(self.bin, "codex-" + name)],
                                  stdout=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(proc.returncode, 0, proc.stdout)


class ConflictTest(CliTestCase):
    def assert_conflict_without_changes(self, *command):
        before = self.snapshot()
        result = self.run_cli(*command)
        self.assertEqual(result.code, 3, result)
        self.assertEqual(before, self.snapshot())
        return result

    def test_unmanaged_launcher(self):
        self.write(os.path.join(self.bin, "codex-hud"), "#!/bin/sh\necho mine\n", 0o755)
        result = self.assert_conflict_without_changes("add", "hud")
        self.assertIn("not managed by multi-codex", result.err)

    def test_shared_item_is_real_directory_or_other_link(self):
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(os.path.join(shared, "skills"))
        os.makedirs(os.path.join(shared, "rules"))
        self.ok("init", "--shared-dir", shared, "--shared-items", "skills,rules")
        self.ok("add", "work")
        os.makedirs(os.path.join(self.root, "work", "skills"))
        self.assert_conflict_without_changes("add", "work", "--shared")
        shutil.rmtree(os.path.join(self.root, "work", "skills"))
        os.symlink(self.tmp, os.path.join(self.root, "work", "rules"))
        self.assert_conflict_without_changes("add", "work", "--shared")

    def test_change_root_with_accounts(self):
        self.ok("add", "work")
        self.assert_conflict_without_changes("init", "--root", os.path.join(self.tmp, "other"))


class SharedTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.shared = os.path.join(self.tmp, "shared")
        os.makedirs(os.path.join(self.shared, "skills"))
        self.write(os.path.join(self.shared, "skills", "s.md"))
        self.write(os.path.join(self.shared, "AGENTS.md"))
        self.ok("init", "--shared-dir", self.shared, "--shared-items", "AGENTS.md,skills,rules")

    def managed_links(self, name):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle)["accounts"][name]["managed_links"]

    def test_toggle_shared(self):
        self.ok("add", "work")
        account = os.path.join(self.root, "work")
        result = self.ok("add", "work", "--shared")
        self.assertIn("not present in shared dir", result.out)  # rules 不在共享目录里
        self.assertTrue(os.path.islink(os.path.join(account, "skills")))
        self.assertEqual(self.managed_links("work"), ["AGENTS.md", "skills"])
        self.ok("add", "work", "--no-shared")
        self.assertFalse(os.path.lexists(os.path.join(account, "skills")))
        self.assertEqual(self.managed_links("work"), [])
        self.assertTrue(os.path.exists(os.path.join(self.shared, "skills", "s.md")))
        self.ok("add", "work", "--shared")
        self.assertTrue(os.path.islink(os.path.join(account, "AGENTS.md")))

    def test_user_link_is_not_removed(self):
        self.ok("add", "work")
        account = os.path.join(self.root, "work")
        os.symlink(os.path.join(self.shared, "skills"), os.path.join(account, "skills"))
        result = self.ok("add", "work", "--shared")
        # 动作行里的路径缩写成 ~/…（feature-onboarding-hints A6）。
        self.assertIn("unchanged shared-link ~/.cx/work/skills", result.out)
        self.assertEqual(self.managed_links("work"), ["AGENTS.md"])
        self.ok("add", "work", "--no-shared")
        self.assertTrue(os.path.islink(os.path.join(account, "skills")))
        self.assertFalse(os.path.lexists(os.path.join(account, "AGENTS.md")))

    def _user_links(self, items):
        account = os.path.join(self.root, "work")
        for item in items:
            os.symlink(os.path.join(self.shared, item), os.path.join(account, item))
        return account

    def test_adopt_existing_links(self):
        """§8 第 1、2 条：接管用户自建的软链，不重建；再次执行全部 unchanged；接管后关闭共享会删除它们。"""
        self.ok("add", "work")
        account = self._user_links(["AGENTS.md", "skills"])
        before = {item: os.lstat(os.path.join(account, item)).st_ino for item in ("AGENTS.md", "skills")}
        result = self.ok("add", "work", "--shared", "--adopt")
        self.assertIn("update shared-link ~/.cx/work/skills (adopted)", result.out)
        self.assertEqual(self.managed_links("work"), ["AGENTS.md", "skills"])
        after = {item: os.lstat(os.path.join(account, item)).st_ino for item in ("AGENTS.md", "skills")}
        self.assertEqual(before, after, "adopted links must not be recreated")
        again = self.ok("add", "work", "--shared", "--adopt")
        self.assertNotIn("] update", again.out)
        self.ok("add", "work", "--no-shared")
        self.assertFalse(os.path.lexists(os.path.join(account, "skills")))
        self.assertFalse(os.path.lexists(os.path.join(account, "AGENTS.md")))
        self.assertTrue(os.path.exists(os.path.join(self.shared, "skills", "s.md")))

    def test_adopt_on_already_shared_account(self):
        self.ok("add", "work")
        account = self._user_links(["skills"])
        self.ok("add", "work", "--shared")
        self.assertEqual(self.managed_links("work"), ["AGENTS.md"])
        self.ok("add", "work", "--adopt")
        self.assertEqual(self.managed_links("work"), ["AGENTS.md", "skills"])
        self.assertTrue(os.path.islink(os.path.join(account, "skills")))

    def test_adopt_requires_sharing(self):
        """§8 第 4 条。"""
        self.ok("add", "work")
        self._user_links(["skills"])
        before = self.snapshot()
        result = self.run_cli("add", "work", "--adopt")
        self.assertEqual(result.code, 2, result)
        self.assertIn("--adopt requires sharing", result.err)
        self.assertEqual(before, self.snapshot())

    def test_adopt_does_not_bypass_conflicts(self):
        """§8 第 5、6 条：其它项冲突时什么都不接管；指向别处的软链仍是冲突。"""
        self.ok("add", "work")
        account = self._user_links(["skills"])
        os.makedirs(os.path.join(self.shared, "rules"))
        os.symlink(self.tmp, os.path.join(account, "rules"))
        before = self.snapshot()
        result = self.run_cli("add", "work", "--shared", "--adopt")
        self.assertEqual(result.code, 3, result)
        self.assertEqual(before, self.snapshot())
        os.unlink(os.path.join(account, "rules"))
        os.makedirs(os.path.join(account, "rules"))
        self.assertEqual(self.run_cli("add", "work", "--shared", "--adopt").code, 3)
        self.assertEqual(self.managed_links("work"), [])

    def test_item_removed_from_shared_items(self):
        self.ok("add", "work", "--shared")
        self.ok("init", "--shared-items", "AGENTS.md")
        self.assertFalse(os.path.lexists(os.path.join(self.root, "work", "skills")))
        self.assertTrue(os.path.islink(os.path.join(self.root, "work", "AGENTS.md")))
        self.assertTrue(os.path.isdir(os.path.join(self.shared, "skills")))

    def test_change_shared_dir(self):
        self.ok("add", "work", "--shared")
        other = os.path.join(self.tmp, "shared2")
        shutil.copytree(self.shared, other)
        self.ok("init", "--shared-dir", other)
        link = os.path.join(self.root, "work", "skills")
        self.assertEqual(os.path.realpath(link), os.path.join(other, "skills"))


class AdoptExistingTest(CliTestCase):
    def test_existing_cx_layout(self):
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(os.path.join(shared, "skills"))
        os.makedirs(os.path.join(self.root, ".migration"))
        gmail = os.path.join(self.root, "a@gmail.com")
        outlook = os.path.join(self.root, "b@outlook.com")
        self.write(os.path.join(gmail, "auth.json"), "{}")
        os.symlink(os.path.join(shared, "skills"), os.path.join(gmail, "skills"))
        os.makedirs(os.path.join(outlook, "skills", ".system"))
        before = self.snapshot(self.root)
        self.ok("init", "--shared-dir", shared, "--shared-items", "skills")
        self.ok("add", "a@gmail.com", "--proxy", "7901")
        self.ok("add", "b@outlook.com")
        self.assertEqual(before, self.snapshot(self.root))
        listing = self.ok("list").out
        self.assertNotIn(".migration", listing)
        self.ok("add", "a@gmail.com", "--shared")
        before = self.snapshot(self.root)
        self.assertEqual(self.run_cli("add", "b@outlook.com", "--shared").code, 3)
        self.assertEqual(before, self.snapshot(self.root))


class ApplyRemoveInitTest(CliTestCase):
    def test_bin_dir_change_moves_launchers(self):
        self.ok("add", "work")
        new_bin = os.path.join(self.tmp, "bin2")
        self.ok("init", "--bin-dir", new_bin)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "codex-work")))
        self.assertTrue(os.path.exists(os.path.join(new_bin, "codex-work")))

    def test_apply_file_drops_account(self):
        self.ok("add", "a")
        self.ok("add", "b")
        path = self.write(os.path.join(self.tmp, "c.json"), json.dumps({"version": 1, "accounts": {"a": {}}}))
        self.ok("apply", "-f", path)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "codex-b")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "b")))

    def test_remove_unregistered_cleans_orphan(self):
        self.ok("add", "a")
        with open(os.path.join(self.state, "config.json")) as handle:
            data = json.load(handle)
        data["accounts"] = {}
        self.write(os.path.join(self.state, "config.json"), json.dumps(data))
        result = self.ok("remove", "a")
        self.assertIn("not registered", result.out)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "codex-a")))

    def test_apply_conflict_leaves_config_untouched(self):
        self.ok("add", "a")
        self.write(os.path.join(self.bin, "codex-b"), "#!/bin/sh\n", 0o755)
        path = self.write(os.path.join(self.tmp, "c.json"), json.dumps(
            {"version": 1, "accounts": {"a": {}, "b": {}}}))
        before = self.snapshot()
        self.assertEqual(self.run_cli("apply", "-f", path).code, 3)
        self.assertEqual(before, self.snapshot())

    def test_apply_root_change_conflict(self):
        self.ok("add", "a")
        path = self.write(os.path.join(self.tmp, "c.json"), json.dumps(
            {"version": 1, "root": os.path.join(self.tmp, "r2"), "accounts": {"a": {}}}))
        self.assertEqual(self.run_cli("apply", "-f", path).code, 3)

    def test_apply_case_only_rename_is_conflict(self):
        self.ok("add", "work")
        path = self.write(os.path.join(self.tmp, "c.json"), json.dumps({"version": 1, "accounts": {"Work": {}}}))
        before = self.snapshot()
        self.assertEqual(self.run_cli("apply", "-f", path).code, 3)
        self.assertEqual(before, self.snapshot())

    def test_apply_case_duplicate(self):
        path = self.write(os.path.join(self.tmp, "c.json"), json.dumps(
            {"version": 1, "accounts": {"Work": {}, "work": {}}}))
        self.assertEqual(self.run_cli("apply", "-f", path).code, 1)

    def test_apply_ignores_managed_links_in_file(self):
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(os.path.join(shared, "skills"))
        self.ok("init", "--shared-dir", shared, "--shared-items", "skills")
        self.ok("add", "a")
        user_link = os.path.join(self.root, "a", "skills")
        os.symlink(os.path.join(shared, "skills"), user_link)
        path = self.write(os.path.join(self.tmp, "c.json"), json.dumps({
            "version": 1, "shared": {"dir": shared, "items": ["skills"]},
            "accounts": {"a": {"shared": True, "managed_links": ["skills"]}}}))
        self.ok("apply", "-f", path)
        self.ok("add", "a", "--no-shared")
        self.assertTrue(os.path.islink(user_link), "a link the user created must survive")

    def test_corrupted_journal(self):
        self.write(os.path.join(self.state, "migrate-journal.json"), "{not json")
        for command in (("list",), ("add", "a"), ("migrate-default", "main")):
            with self.subTest(command=command):
                result = self.run_cli(*command)
                self.assertEqual(result.code, 1, result)
                self.assertIn("delete the journal file", result.err)
                self.assertNotIn("Traceback", result.err)

    def test_pending_journal_blocks_other_writes(self):
        self.write(os.path.join(self.state, "migrate-journal.json"), json.dumps(
            {"name": "main", "source": "/nonexistent", "target": "/x", "backup": "/y",
             "mode": "rename", "phase": "planned"}))
        self.assertEqual(self.run_cli("add", "a").code, 1)
        self.ok("list")


class LockTest(CliTestCase):
    def test_lock_busy_and_released_after_kill(self):
        holder_code = ("import os, time, sys; sys.path.insert(0, {!r}); "
                       "from multi_codex.lock import WriteLock\n"
                       "with WriteLock():\n    print('locked', flush=True); time.sleep(60)").format(SRC)
        env = dict(self.env)
        holder = subprocess.Popen([sys.executable, "-c", holder_code], env=env,
                                  stdout=subprocess.PIPE, universal_newlines=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            result = self.run_cli("add", "a")
            self.assertEqual(result.code, 1)
            self.assertIn("pid {}".format(holder.pid), result.err)
        finally:
            holder.send_signal(signal.SIGKILL)
            holder.wait()
            holder.stdout.close()
        self.ok("add", "a")

    def test_stale_pid_in_lock_file(self):
        os.makedirs(self.state, exist_ok=True)
        self.write(os.path.join(self.state, "lock"), "1")  # PID 1 永远存活，但并不持有锁
        self.ok("add", "a")


class EnvironmentWarningTest(CliTestCase):
    def test_isolation_breaking_variables(self):
        self.ok("add", "a")
        result = self.ok("list", env={"CODEX_SQLITE_HOME": "/tmp/x"})
        self.assertIn("CODEX_SQLITE_HOME", result.err)


if __name__ == "__main__":
    unittest.main()
