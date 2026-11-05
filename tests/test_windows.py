"""feature-windows-support §8：Windows 支持的回归。

WindowsTest 系列只在原生 Windows 上运行（CI 的 windows-latest 行只跑本文件），对应方案 §8.1；
在 macOS·Linux 上整类跳过。PosixRegressionTest 在 macOS·Linux 上运行，对应 §8.2 第 2、6 条：
Windows 的账号环境与 sh 启动命令语义一致、改名遇 PermissionError 时 POSIX 仍走基线路径。
"""

import errno
import io
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import IS_WINDOWS, ROOT, SRC, CliTestCase

sys.path.insert(0, SRC)

from multi_codex import (accounts, cli, completion, fsutil, identity, launcher, lock, migrate,  # noqa: E402
                         platform, router, usage)
from multi_codex.config import validate_env_key  # noqa: E402

CRASH = 137
WINDOWS_ONLY = unittest.skipUnless(IS_WINDOWS, "Windows only")
POSIX_ONLY = unittest.skipIf(IS_WINDOWS, "POSIX only")

# 与 sh 版启动命令的输出逐字比较（launcher.render 里的 printf / echo 文案）。
SH_MISSING_DIR = "multi-codex: account directory does not exist: {}\n"
SH_NO_CODEX = "multi-codex: codex not found in PATH\n"

# 真 JS 版 codex.js：以 JSON 打印收到的参数与关心的环境变量，退出码取 FAKE_EXIT。
CODEX_JS = r"""
const fs = require("fs");
const keys = ["CODEX_HOME", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
              "CODEX_SQLITE_HOME", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"];
const env = {};
for (const key of keys) { if (process.env[key] !== undefined) env[key] = process.env[key]; }
fs.writeFileSync(process.env.FAKE_CODEX_OUT + ".json", JSON.stringify({argv: process.argv.slice(2), env: env}));
process.exit(parseInt(process.env.FAKE_EXIT || "0", 10));
"""


class WindowsBase(CliTestCase):
    def setUp(self):
        super().setUp()
        # 占用检查照常执行、结果为“无占用”，但不运行 PowerShell（方案 §5.1.10）。
        self.process_list = self.write(os.path.join(self.tmp, "processes.json"), "[]")
        self.env["MULTI_CODEX_TEST_PROCESS_LIST"] = self.process_list
        self.link = os.path.join(self.home, ".codex")

    def cmd(self, name):
        return os.path.join(self.bin, "codex-" + name + ".cmd")

    def in_process(self):
        """进程内调用 CLI 时使用与子进程相同的隔离环境。"""
        patcher = mock.patch.dict(os.environ, self.env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("MULTI_CODEX_TEST_CRASH_AT", "MULTI_CODEX_TEST_FAIL_AT", "CODEX_HOME"):
            os.environ.pop(name, None)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def use_journal(self):
        return os.path.join(self.state, "use-journal.json")

    def add_two(self):
        for name in ("a", "b"):
            self.ok("add", name)
            self.write(os.path.join(self.root, name, "auth.json"), "{}")
            self.write(os.path.join(self.root, name, "state_5.sqlite"), name)

    def points_to(self, name):
        return platform.is_link(self.link) and os.path.normcase(os.path.realpath(self.link)) == \
            os.path.normcase(os.path.realpath(os.path.join(self.root, name)))


@WINDOWS_ONLY
class PlatformTest(WindowsBase):
    def test_01_flags(self):
        self.assertTrue(platform.IS_WINDOWS)
        self.assertTrue(platform.is_supported())

    def test_02_launcher_and_router_markers(self):
        text = launcher.render_windows("a", r"C:\py\python.exe", r"C:\py\_entry.py")
        self.assertEqual(len(text.splitlines()), 4)
        os.makedirs(self.bin)
        self.write(self.cmd("a"), text)
        self.write(os.path.join(self.bin, "codex-b"), "#!/bin/sh\n" + launcher.MARKER_PREFIX + "b\n")
        self.assertEqual(launcher.managed_account(self.cmd("a")), "a")
        self.assertEqual(sorted(launcher.scan_managed(self.bin)), ["a"])
        auto = self.write(self.cmd("auto"), router.render_windows(r"C:\py\python.exe", r"C:\py\_entry.py"))
        self.assertEqual(open(auto).read().splitlines()[1], router.WIN_ROUTER_MARKER)
        self.assertTrue(router.is_router(auto))
        self.assertIsNone(launcher.managed_account(auto))

    def test_02_apply_keeps_router(self):
        self.ok("add", "a")
        self.ok("add", "b")
        work = os.path.join(self.tmp, "w")
        os.makedirs(work)
        self.ok("bind", "a", work)
        self.assertTrue(os.path.isfile(self.cmd("auto")))
        self.ok("bind", "b", work)
        self.ok("apply")
        self.assertTrue(router.is_router(self.cmd("auto")))

    def test_03_cmd_unsafe_reason(self):
        with mock.patch.dict(os.environ, {"USERPROFILE": "C:\\Users\\\u5f20\u4e09"}), \
                mock.patch("multi_codex.winapi.short_path", side_effect=lambda path: path):
            rewritten = launcher._cmd_path("C:\\Users\\\u5f20\u4e09\\pipx\\python.exe")
            self.assertEqual(rewritten, "%USERPROFILE%\\pipx\\python.exe")
            self.assertIsNone(launcher.cmd_unsafe_reason(["a"], [rewritten]))
            stuck = launcher._cmd_path("D:\\\u4e2d\u6587\\python.exe")
            self.assertIn("non-ASCII", launcher.cmd_unsafe_reason(["a"], [stuck]))
        for char in ('"', "%", "!"):
            self.assertIn("cannot be written safely", launcher.cmd_unsafe_reason(["a" + char]))

    def test_03_conflict_everywhere(self):
        self.in_process()
        self.assertEqual(self.main("add", "a")[0], 0)
        bad = "D:\\\u4e2d\u6587\\python.exe"
        with mock.patch.object(launcher.sys, "executable", bad), \
                mock.patch("multi_codex.winapi.short_path", side_effect=lambda path: path):
            reason = launcher.windows_unsafe_reason("a")
            self.assertIn("non-ASCII", reason)
            code, _, err = self.main("add", "b")
            self.assertEqual(code, accounts.EXIT_CONFLICT, err)
            self.assertIn(reason.split(",")[0], err)
            from multi_codex.config import load_config
            config, _ = load_config()
            self.assertEqual(accounts.launcher_status(config, "a"), "conflict")

    def test_04_entry(self):
        entry = launcher.entry_script()
        result = subprocess.run([sys.executable, "-I", entry, "--version"], env=self._env_plain(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # 入口脚本放在 tmp 下的 elsewhere 目录，它会把 tmp 追加进搜索路径；tmp 下另放一个假 multi_codex 包，
        # 让 import 成功但加载到别的目录，才能走到“包目录不一致”的分支（CI 未安装本包，否则只会导入失败）
        copy = os.path.join(self.tmp, "elsewhere", "_entry.py")
        os.makedirs(os.path.dirname(copy))
        shutil.copy(entry, copy)
        self.write(os.path.join(self.tmp, "multi_codex", "__init__.py"), "")
        result = subprocess.run([sys.executable, "-I", copy, "--version"], env=self._env_plain(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("instead of", result.stderr)

    def _env_plain(self):
        env = dict(self.env)
        env.pop("PYTHONPATH", None)
        env["PYTHONPATH"] = SRC
        return env

    def test_09_account_env_proxy(self):
        base = {"PATH": "x", "no_proxy": "corp", "http_proxy": "old", "ALL_PROXY": "old"}
        env = launcher.account_env("a", r"C:\a", "inherit", base=base)
        self.assertEqual(env["http_proxy"], "old")
        env = launcher.account_env("a", r"C:\a", "http://127.0.0.1:7901", base=base)
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7901")
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7901")
        self.assertNotIn("ALL_PROXY", env)
        self.assertEqual(env["NO_PROXY"], "corp,localhost,127.0.0.1,::1")
        self.assertEqual([k for k in env if k.upper() == "NO_PROXY"], ["NO_PROXY"])
        env = launcher.account_env("a", r"C:\a", "socks5://127.0.0.1:7902", base=base)
        self.assertEqual(env["ALL_PROXY"], "socks5://127.0.0.1:7902")
        env = launcher.account_env("a", r"C:\a", "off", base=base)
        self.assertFalse([k for k in env if k.upper().endswith("_PROXY")])
        env = launcher.account_env("a", r"C:\a", "http://p:1", base={"NO_PROXY": "a", "no_proxy": "b"})
        self.assertEqual([k for k in env if k.upper() == "NO_PROXY"], ["NO_PROXY"])
        self.assertEqual(env["NO_PROXY"], "a,localhost,127.0.0.1,::1")

    def test_10_env_key_case(self):
        env = launcher.account_env("a", r"C:\a", "inherit", env={"Foo": "1", "FOO": "2"}, base={})
        self.assertEqual([k for k in env if k.upper() == "FOO"], ["FOO"])
        # sh 版按排序依次 export：FOO 先、Foo 后，Windows 不区分大小写时后者的值留下。
        self.assertEqual(env["FOO"], "1")

    def test_11_reserved_env_keys(self):
        for key in ("codex_home", "Http_Proxy"):
            with self.assertRaises(ValueError):
                validate_env_key(key)

    def test_12_junction_and_file_link(self):
        target = os.path.join(self.tmp, "target")
        os.makedirs(target)
        link = os.path.join(self.tmp, "link")
        platform.create_link(target, link)
        self.assertTrue(platform.is_link(link))
        self.assertEqual(fsutil.entry_kind(link), fsutil.KIND_LINK)
        self.assertFalse(platform.read_link(link).startswith("\\\\?\\"))
        platform.remove_link(link)
        self.assertTrue(os.path.isdir(target))
        file_target = self.write(os.path.join(self.tmp, "f.txt"))
        file_link = os.path.join(self.tmp, "f-link")
        if platform.can_create_file_link():
            platform.create_link(file_target, file_link)
            self.assertTrue(platform.is_link(file_link))
        else:
            with self.assertRaises(platform.LinkPermissionError) as caught:
                platform.create_link(file_target, file_link)
            self.assertIn("Developer Mode", str(caught.exception))

    def test_13_file_link_precheck(self):
        self.write(os.path.join(self.home, ".codex-shared", "AGENTS.md"), "x")
        os.makedirs(os.path.join(self.home, ".codex-shared", "skills"))
        before = self.snapshot(self.home)
        result = self.run_cli("add", "a", "--shared", env={"MULTI_CODEX_TEST_FAIL_AT": "file-link-denied"})
        self.assertEqual(result.code, 1, result)
        self.assertIn("nothing was changed", result.err)
        self.assertEqual(before, self.snapshot(self.home))

    def test_14_tree_walks_keep_junctions(self):
        source = os.path.join(self.tmp, "src")
        outside = os.path.join(self.tmp, "outside")
        self.write(os.path.join(outside, "big.txt"), "x")
        self.write(os.path.join(source, "a.txt"), "a")
        platform.create_link(outside, os.path.join(source, "j"))
        manifest, _ = fsutil.build_manifest(source)
        self.assertFalse([key for key in manifest if "big.txt" in key])
        copy = os.path.join(self.tmp, "copy")
        fsutil.copy_tree(source, copy)
        self.assertTrue(platform.is_junction(os.path.join(copy, "j")))
        self.assertFalse(os.path.lexists(os.path.join(copy, "j", "missing")))

    def test_15_lock(self):
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import sys, time; sys.path.insert(0, {!r}); from multi_codex import lock; "
                                   "l = lock.WriteLock(); l.__enter__(); print('ready', flush=True); "
                                   "time.sleep(30)".format(SRC)],
                                  env=dict(self.env), stdout=subprocess.PIPE, universal_newlines=True)
        self.addCleanup(lambda: (holder.kill(), holder.wait()))
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        self.in_process()
        with self.assertRaises(lock.LockBusyError) as caught:
            with lock.WriteLock():
                pass
        # 锁文件里记的是文本 PID（lock.py LockBusyError.holder_pid 为 str）
        self.assertEqual(caught.exception.holder_pid, str(holder.pid))
        holder.kill()
        holder.wait()
        # 持锁进程被杀后，系统释放 LockFile 区段可能稍有延迟：最多重试约 5 秒
        for attempt in range(50):
            try:
                with lock.WriteLock():
                    break
            except lock.LockBusyError:
                if attempt == 49:
                    raise
                time.sleep(0.1)


@WINDOWS_ONLY
class LaunchTest(WindowsBase):
    def setUp(self):
        super().setUp()
        self.ok("add", "a", "--proxy", "http://127.0.0.1:7901")
        self.ok("env", "a", "EXTRA=1")

    def _npm_layout(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node.exe not found in PATH")
        shutil.copy(node, os.path.join(self.fakebin, "node.exe"))
        os.unlink(os.path.join(self.fakebin, "codex.cmd"))
        script = self.write(os.path.join(self.fakebin, "node_modules", "@openai", "codex", "bin", "codex.js"),
                            CODEX_JS)
        self.write(os.path.join(self.fakebin, "codex.cmd"),
                   '@"%~dp0node.exe" "%~dp0node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n')
        return script

    def _received(self):
        with open(self.fake_out + ".json") as handle:
            return json.load(handle)

    def test_05_arguments_reach_codex_unchanged(self):
        self._npm_layout()
        args = ["a b", "x&y", "p|q", "c^d", "%PATH%", 'say "hi"', "tail\\"]
        env = {"CODEX_API_KEY": "leak", "FAKE_EXIT": "7"}
        result = self.run_cli("launch", "a", "--", *args, env=env)
        self.assertEqual(result.code, 7, result)
        received = self._received()
        self.assertEqual(received["argv"], args)
        self.assertEqual(received["env"]["CODEX_HOME"], os.path.join(self.root, "a"))
        self.assertEqual(received["env"]["HTTPS_PROXY"], "http://127.0.0.1:7901")
        self.assertNotIn("CODEX_API_KEY", received["env"])
        plain = ["a b", "plain"]
        proc = subprocess.run([self.cmd("a")] + plain, env=dict(self.env, **env),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertEqual(self._received()["argv"], plain)

    def test_06_cmd_target_fallback(self):
        self._npm_layout()
        os.unlink(os.path.join(self.fakebin, "node_modules", "@openai", "codex", "bin", "codex.js"))
        self.write(os.path.join(self.fakebin, "codex.cmd"),
                   '@"{}" "{}" %*\r\n'.format(sys.executable, os.path.join(self.tmp, "fake_codex.py")))
        resolved = launcher.resolve_command(["codex", "x"], {"PATH": self.fakebin})
        self.assertEqual(os.path.basename(resolved[0]).lower(), "codex.cmd")
        code, args, _, err = self.run_launcher("a", "one", "two")
        self.assertEqual((code, args), (0, ["one", "two"]), err)
        for readme in ("README.md", "README.zh-CN.md"):
            with open(os.path.join(ROOT, readme), encoding="utf-8") as handle:
                self.assertIn("codex.cmd", handle.read())

    def test_07_launch_interception(self):
        code, args, _, err = self.run_launcher("a", "--help")
        self.assertEqual((code, args), (0, ["--help"]), err)
        result = self.run_cli("launch", "a")
        self.assertEqual(result.code, 2, result)
        self.assertNotIn("launch", self.ok("-h").out.split())
        self.assertNotIn("'launch'", self.ok("completion", "powershell").out)
        shutil.rmtree(os.path.join(self.root, "a"))
        result = self.run_cli("launch", "a", "--", "x")
        self.assertEqual((result.code, result.err), (1, SH_MISSING_DIR.format(os.path.join(self.root, "a"))))
        os.makedirs(os.path.join(self.root, "a"))
        os.unlink(os.path.join(self.fakebin, "codex.cmd"))
        result = self.run_cli("launch", "a", "--", "x")
        self.assertEqual((result.code, result.err), (127, SH_NO_CODEX))

    def test_08_router(self):
        work = os.path.join(self.tmp, "w")
        other = os.path.join(self.tmp, "o")
        os.makedirs(work)
        os.makedirs(other)
        self.ok("add", "b")
        self.ok("bind", "a", work)
        auto = self.cmd("auto")
        run = lambda cwd: subprocess.run([auto, "x"], env=dict(self.env), cwd=cwd, stdout=subprocess.PIPE,  # noqa
                                         stderr=subprocess.PIPE, universal_newlines=True)
        proc = run(work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        received = open(self.fake_out + ".env").read()
        self.assertIn("CODEX_HOME=" + os.path.join(self.root, "a"), received)
        # 改绑定后不 apply，立即生效。
        self.ok("bind", "b", work)
        self.assertEqual(run(work).returncode, 0)
        self.assertIn("CODEX_HOME=" + os.path.join(self.root, "b"), open(self.fake_out + ".env").read())
        proc = run(other)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no account is bound to", proc.stderr)
        self.assertNotIn("CODEX_HOME=", open(self.fake_out + ".env").read())
        os.unlink(os.path.join(self.fakebin, "codex.cmd"))
        self.assertEqual(run(other).returncode, 127)


@WINDOWS_ONLY
class SwitchTest(WindowsBase):
    def setUp(self):
        super().setUp()
        self.add_two()

    def test_16_busy(self):
        listing = [
            {"ProcessId": 4101, "Name": "codex.exe", "CommandLine": "codex.exe", "ExecutablePath": r"C:\c.exe"},
            {"ProcessId": 4102, "Name": "node.exe", "ExecutablePath": r"C:\node.exe",
             "CommandLine": r'"C:\node.exe" "C:\npm\node_modules\@openai\codex\bin\codex.js"'},
            {"ProcessId": 4103, "Name": "node.exe", "CommandLine": r"node C:\other\app.js", "ExecutablePath": ""},
        ]
        self.write(self.process_list, json.dumps(listing))
        platform.create_link(os.path.join(self.root, "a"), self.link)
        for args in (("use", "b"), ("restore", "a")):
            result = self.run_cli(*args)
            self.assertEqual(result.code, accounts.EXIT_BUSY, result)
            self.assertIn("pid=4101", result.out + result.err)
            self.assertIn("pid=4102", result.out + result.err)
            self.assertNotIn("pid=4103", result.out + result.err)
        result = self.run_cli("use", "b", env={"MULTI_CODEX_TEST_FAIL_AT": "busy-check-powershell"})
        self.assertNotEqual(result.code, 0, result)
        self.assertIn("powershell", result.err)
        found = platform._find_busy_windows(set())
        self.assertIsInstance(found, list)

    def test_16_busy_migrate(self):
        self.write(os.path.join(self.link, "auth.json"), "{}")
        self.write(self.process_list, json.dumps([{"ProcessId": 4101, "Name": "codex.exe"}]))
        result = self.run_cli("migrate-default", "main")
        self.assertEqual(result.code, accounts.EXIT_BUSY, result)

    def test_17_use(self):
        self.write(self.use_journal(), json.dumps({"link": self.link, "old_target": "x", "new_target": "y"}))
        self.ok("use", "a")
        self.assertTrue(self.points_to("a"))
        self.assertFalse(os.path.exists(self.use_journal()))
        for name in ("b", "a", "b"):
            self.ok("use", name)
            self.assertTrue(self.points_to(name))
            self.assertFalse(os.path.exists(self.use_journal()))
        result = self.run_cli("use", "a", env={"MULTI_CODEX_TEST_FAIL_AT": "use-link"})
        self.assertEqual(result.code, 1, result)
        self.assertTrue(self.points_to("b"))
        self.assertFalse(os.path.exists(self.use_journal()))
        result = self.run_cli("use", "a", env={"MULTI_CODEX_TEST_FAIL_AT": "use-remove"})
        self.assertEqual(result.code, 1, result)
        self.assertTrue(self.points_to("b"))
        self.assertFalse(os.path.exists(self.use_journal()))

    def _crash_between(self):
        self.ok("use", "a")
        result = self.run_cli("use", "b", env={"MULTI_CODEX_TEST_CRASH_AT": "use-unlinked"})
        self.assertEqual(result.code, CRASH, result)
        self.assertFalse(os.path.lexists(self.link))
        self.assertTrue(os.path.exists(self.use_journal()))

    def test_18_crash_and_recover(self):
        self._crash_between()
        journal = open(self.use_journal()).read()
        result = self.ok("use", "b", "--dry-run")
        self.assertIn("(dry-run) would recover interrupted switch", result.out)
        self.assertEqual(journal, open(self.use_journal()).read())
        self.assertFalse(os.path.lexists(self.link))
        result = self.run_cli("migrate-default", "main")
        self.assertEqual(result.code, 1, result)
        self.assertIn("multi-codex use", result.err)
        result = self.run_cli("doctor")
        self.assertIn("interrupted switch", result.out + result.err)
        self.ok("use", "b")
        self.assertTrue(self.points_to("b"))
        self.assertFalse(os.path.exists(self.use_journal()))

    def test_18_recreated_directory(self):
        self._crash_between()
        os.makedirs(self.link)
        result = self.run_cli("use", "b")
        self.assertEqual(result.code, 1, result)
        self.assertIn("was recreated as a directory", result.err)
        self.assertTrue(os.path.exists(self.use_journal()))

    def test_18_restore_after_crash(self):
        self._crash_between()
        self.ok("restore", "a")
        self.assertFalse(os.path.exists(self.use_journal()))
        self.assertTrue(os.path.isdir(self.link) and not platform.is_link(self.link))

    def test_18_restore_journal_wins(self):
        self.ok("use", "a")
        result = self.run_cli("restore", "a", env={"MULTI_CODEX_TEST_CRASH_AT": "restore-unlinked"})
        self.assertEqual(result.code, CRASH, result)
        self.assertFalse(os.path.lexists(self.link))
        self.write(self.use_journal(), json.dumps({"link": self.link, "old_target": os.path.join(self.root, "a"),
                                                   "new_target": os.path.join(self.root, "b")}))
        self.ok("restore", "a")
        self.assertFalse(os.path.exists(self.use_journal()))
        self.assertTrue(os.path.isdir(self.link) and not platform.is_link(self.link))
        self.assertFalse(os.path.exists(os.path.join(self.state, "restore-journal.json")))

    def test_20_restore(self):
        self.ok("use", "a")
        result = self.run_cli("restore", "a", env={"MULTI_CODEX_TEST_SHARING_AT": "restore-rename:99"})
        self.assertEqual(result.code, 1, result)
        self.assertIn("is in use by another program", result.err)
        self.ok("restore", "a")
        self.assertTrue(os.path.isdir(self.link) and not platform.is_link(self.link))


@WINDOWS_ONLY
class MigrateTest(WindowsBase):
    def setUp(self):
        super().setUp()
        self.target = os.path.join(self.root, "main")
        self.write(os.path.join(self.link, "auth.json"), "{}")
        self.write(os.path.join(self.link, "sessions", "2026", "a.jsonl"), "line\n")

    def migrate(self, *extra, env=None):
        return self.run_cli("migrate-default", "main", *extra, env=env)

    def assert_migrated(self):
        self.assertTrue(platform.is_link(self.link))
        self.assertEqual(os.path.normcase(os.path.realpath(self.link)), os.path.normcase(self.target))
        self.assertIsNone(self.journal())
        self.assertTrue(os.path.isfile(os.path.join(self.target, "sessions", "2026", "a.jsonl")))

    def assert_untouched(self):
        self.assertTrue(os.path.isdir(self.link) and not platform.is_link(self.link))
        self.assertIsNone(self.journal())
        self.assertFalse(os.path.exists(self.target))

    def test_19_success_and_retry(self):
        result = self.migrate(env={"MULTI_CODEX_TEST_SHARING_AT": "migrate-move:3"})
        self.assertEqual(result.code, 0, result)
        self.assert_migrated()

    def test_19_move_in_use(self):
        result = self.migrate(env={"MULTI_CODEX_TEST_SHARING_AT": "migrate-move:99"})
        self.assertEqual(result.code, accounts.EXIT_BUSY, result)
        self.assertIn("is in use by another program", result.err)
        self.assert_untouched()

    def test_19_park_in_use(self):
        result = self.migrate("--copy", env={"MULTI_CODEX_TEST_SHARING_AT": "migrate-park:99"})
        self.assertEqual(result.code, accounts.EXIT_BUSY, result)
        self.assert_untouched()

    def test_19_rollback_in_use(self):
        result = self.migrate(env={"MULTI_CODEX_TEST_FAIL_AT": "rename-target",
                                   "MULTI_CODEX_TEST_SHARING_AT": "migrate-rollback:99"})
        self.assertEqual(result.code, 1, result)
        self.assertIsNotNone(self.journal())
        self.assertIn("winerror", result.err)
        self.assertEqual(self.migrate().code, 0)
        self.assert_migrated()

    def test_19_rollback_restore_in_use(self):
        result = self.migrate("--copy", env={"MULTI_CODEX_TEST_FAIL_AT": "journal-parked",
                                             "MULTI_CODEX_TEST_SHARING_AT": "migrate-rollback-restore:99"})
        self.assertEqual(result.code, 1, result)
        self.assertIsNotNone(self.journal())
        self.assertIn("winerror", result.err)
        result = self.migrate("--copy")
        self.assertEqual(result.code, 0, result)
        self.assertIn("previous rollback finished", result.out + result.err)
        self.assert_migrated()


@WINDOWS_ONLY
class LiveUsageTest(WindowsBase):
    def test_21_close_without_taskkill(self):
        # 读 stdin 到 EOF 就自行退出的假程序：close 关掉 stdin 后它正常退出，不应再调用 taskkill
        script = self.write(os.path.join(self.tmp, "polite.py"), "import sys\nsys.stdin.read()\n")
        real_popen = subprocess.Popen
        with mock.patch.object(usage.subprocess, "Popen",
                               side_effect=lambda argv, **kw: real_popen([sys.executable, script], **kw)):
            server = usage._AppServer("unused", 10)
        real_run = subprocess.run
        calls = []

        def counting(command, *args, **kwargs):
            if os.path.basename(command[0]).lower() == "taskkill.exe":
                calls.append(command)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(usage.subprocess, "run", counting):
            server.close(True)
        self.assertEqual(calls, [])

    def test_21_close_kills_tree(self):
        script = self.write(os.path.join(self.tmp, "stubborn.py"),
                            "import subprocess, sys, time\n"
                            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                            "open(sys.argv[1], 'w').write(str(child.pid))\n"
                            "time.sleep(60)\n")
        pid_file = os.path.join(self.tmp, "child.pid")
        # usage.subprocess 就是 subprocess 模块，打补丁前先拿到真的 Popen，否则 side_effect 会递归调用 mock
        real_popen = subprocess.Popen
        for fail in ("", "job-assign-failed"):
            with mock.patch.dict(os.environ, {"MULTI_CODEX_TEST_FAIL_AT": fail}):
                with mock.patch.object(usage.subprocess, "Popen",
                                       side_effect=lambda argv, **kw: real_popen(
                                           [sys.executable, script, pid_file], **kw)):
                    server = usage._AppServer("unused", 10)
            for _ in range(100):
                if os.path.exists(pid_file) and open(pid_file).read():
                    break
                time.sleep(0.05)
            child = int(open(pid_file).read())
            real_run = subprocess.run
            calls = []

            def counting(command, *args, **kwargs):
                calls.append(command)
                return real_run(command, *args, **kwargs)

            with mock.patch.object(usage.subprocess, "run", counting):
                server.close(False)
            self.assertEqual(len(calls), 1, calls)
            self.assertFalse(platform._pid_alive(server.proc.pid))
            self.assertFalse(platform._pid_alive(child))
            os.unlink(pid_file)

    def test_21_live_end_to_end(self):
        """§8.1-21：假 app-server 起孙进程且不读 stdin；超时后整棵进程树（含 job 失败时）都已退出。"""
        self.ok("add", "a")
        pids_file = os.path.join(self.tmp, "pids")
        for fail in ("", "job-assign-failed"):
            result = self.run_cli("usage", "a", "--live", "--json", "--timeout", "5",
                                  env={"MULTI_CODEX_TEST_FAIL_AT": fail, "FAKE_APP_SERVER_MODE": "hang",
                                       "FAKE_APP_SERVER_PIDS": pids_file})
            self.assertEqual(result.code, 1, result)
            self.assertIn("within 5s", json.loads(result.out)["accounts"][0]["error"])
            with open(pids_file) as handle:
                pids = [int(pid) for pid in handle.read().split()]
            self.assertEqual(len(pids), 2, pids)
            for pid in pids:
                for _ in range(50):
                    if not platform._pid_alive(pid):
                        break
                    time.sleep(0.1)
                self.assertFalse(platform._pid_alive(pid), (fail, pid))
            os.unlink(pids_file)


@WINDOWS_ONLY
class MiscTest(WindowsBase):
    def test_22_system_config(self):
        with mock.patch.dict(os.environ, {"ProgramData": self.tmp}):
            os.environ.pop(identity.SYSTEM_CONFIG_ENV, None)
            self.assertEqual(identity._system_config_path(),
                             os.path.join(self.tmp, "OpenAI", "Codex", "config.toml"))

    def test_23_resume_commands(self):
        session = mock.Mock(session_id="0001", directory="C:\\it's")
        registered = cli._SessionSource("a", "x", "codex-a", True)
        self.assertEqual(cli._resume_command(registered, session), "Set-Location 'C:\\it''s'; codex-a resume 0001")
        default = cli._SessionSource("default", "x", "codex", False)
        session = mock.Mock(session_id="0001", directory=None)
        command = cli._resume_command(default, session)
        self.assertEqual(command, "cmd /d /c 'set CODEX_HOME=& set CODEX_SQLITE_HOME=& set CODEX_API_KEY=& "
                                  "set CODEX_ACCESS_TOKEN=& codex resume 0001'")
        env = dict(self.env, CODEX_HOME="x", CODEX_SQLITE_HOME="x", CODEX_API_KEY="x", CODEX_ACCESS_TOKEN="x")
        powershell = platform.system_program("powershell.exe")
        proc = subprocess.run([powershell, "-NoProfile", "-Command", command + "; $env:CODEX_HOME"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "x")
        received = open(self.fake_out + ".env").read()
        for key in ("CODEX_HOME", "CODEX_SQLITE_HOME", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            self.assertNotIn(key + "=", received)

    def test_24_completion(self):
        text = self.ok("completion", "powershell").out
        self.assertTrue(all(ord(char) < 128 for char in text))
        self.assertNotIn("'launch'", text)
        self.assertIn("powershell", completion.SHELLS)

    def test_24b_completion_values(self):
        # feature-completion-values §8 PowerShell：TabExpansion2 驱动补全器，补全器里调用的 multi-codex 转发到本仓库。
        self.write(os.path.join(self.fakebin, "multi-codex.cmd"), '@"%FAKE_PYTHON%" -m multi_codex %*\r\n')
        for name in ("jakoes.wu@icloud.com", "jakoes.wu@gmail.com", "work"):
            self.ok("add", name)
        script = self.write(os.path.join(self.tmp, "completion.ps1"), self.ok("completion", "powershell").out)
        lines = ["multi-codex proxy work ", "multi-codex completion ", "multi-codex netcheck work ",
                 "multi-codex netcheck work strict --clash ", "multi-codex add new --config-from jak"]
        command = ("Invoke-Expression ([IO.File]::ReadAllText('{}')); foreach ($line in @({})) {{ "
                   "$m = (TabExpansion2 -inputScript $line -cursorColumn $line.Length).CompletionMatches; "
                   "'==' + (($m | ForEach-Object {{ $_.CompletionText }}) -join ' ') }}").format(
                       script.replace("'", "''"), ", ".join("'{}'".format(line) for line in lines))
        env = dict(self.env, PATHEXT=os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"))
        powershell = platform.system_program("powershell.exe")
        proc = subprocess.run([powershell, "-NoProfile", "-Command", command], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        results = [line[2:].split() for line in proc.stdout.splitlines() if line.startswith("==")]
        # 第一条先证明补全器确实被调到，后面几条的结果才有意义。
        self.assertEqual(results[0], ["off", "inherit"], proc.stdout + proc.stderr)
        self.assertEqual(results[1], list(completion.SHELLS))
        self.assertEqual(results[2], ["off", "basic", "strict"])
        self.assertEqual(results[3], ["on", "off"])
        self.assertEqual(sorted(results[4]), ["jakoes.wu@gmail.com", "jakoes.wu@icloud.com"])

    def test_25_doctor_ignores_current_directory(self):
        os.unlink(os.path.join(self.fakebin, "codex.cmd"))
        here = os.path.join(self.tmp, "here")
        self.write(os.path.join(here, "codex.cmd"), "@echo codex-cli 0.1\r\n")
        result = self.run_cli("doctor", cwd=here)
        self.assertIn("codex", result.out)
        self.assertIn("not found", result.out + result.err)

    def test_26_app_is_macos_only(self):
        result = self.run_cli("app", "a")
        self.assertNotEqual(result.code, 0, result)
        self.assertIn("macOS", result.err)


# §8.2-3：生成 POSIX 启动命令（codex-<名称>、codex-auto）全部内容的脚本，覆盖代理与环境变量的若干组合。
# 期望文本 tests/data/posix-launchers-44cb4c1.json 是用基线 44cb4c1 的旧代码跑这段脚本得到的，固定下来不再改：
#   git archive 44cb4c1 src/multi_codex | tar -x -C <空目录>
#   HOME=/home/u python3 -I -c "import sys; sys.path.insert(0, '<空目录>/src'); exec(open('tests/test_windows.py')
#       .read().split('RENDER_CASES = r\'\'\'')[1].split('\'\'\'')[0])" > tests/data/posix-launchers-44cb4c1.json
# 本次改动后 POSIX 上生成的内容必须与它逐字节一致，否则升级后所有启动命令都会被判为过期。
RENDER_CASES = r'''
import json
from multi_codex import launcher, router
from multi_codex.config import parse_config

cases = {}
combos = [
    ("plain", "inherit", None, False),
    ("off", "off", None, False),
    ("http", "http://127.0.0.1:7901", None, False),
    ("socks-env", "socks5h://user:p%40ss@[::1]:1080", {"EXTRA": "1", "QUOTE": "it's $HOME `x`"}, False),
    ("env-only", "inherit", {"A_B": "x y", "EMPTY": ""}, False),
    ("command-mode", "http://proxy.local:8080", {"EXTRA": "1"}, True),
]
for key, proxy, env, command_mode in combos:
    cases["launcher-" + key] = launcher.render("work", "/home/u/.cx/work", proxy, env, command_mode)
cases["launcher-odd-name"] = launcher.render("a.b-c_D", "/home/u/dir with space/it's", "inherit")
configs = {
    "empty": {"accounts": {}},
    "bound": {"accounts": {"work": {}, "home": {"proxy": "off"}},
              "bindings": {"/": "home", "/home/u/src": "work", "/home/u/src/it's dir": "home",
                           "/home/u/gone": "ghost"}},
}
for key, data in configs.items():
    cases["router-" + key] = router.render(parse_config(json.dumps(data), "test"))
print(json.dumps(cases, indent=1, sort_keys=True, ensure_ascii=False))
'''


@POSIX_ONLY
class PosixRegressionTest(CliTestCase):
    """§8.2 第 2、6 条。"""

    SHELL_VARS = ("PWD", "OLDPWD", "SHLVL", "_")

    def test_launcher_bytes_match_baseline(self):
        """§8.2-3：POSIX 上 codex-<名称> 与 codex-auto 的内容与基线 44cb4c1 逐字节一致。"""
        with open(os.path.join(ROOT, "tests", "data", "posix-launchers-44cb4c1.json"), encoding="utf-8") as handle:
            expected = handle.read()
        code = "import sys; sys.path.insert(0, {!r}); exec({!r})".format(SRC, RENDER_CASES)
        result = subprocess.run([sys.executable, "-I", "-c", code], env={"HOME": "/home/u", "PATH": os.defpath},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        for key, text in json.loads(expected).items():
            with self.subTest(case=key):
                self.assertEqual(actual.get(key), text)
        self.assertEqual(sorted(actual), sorted(json.loads(expected)))

    def _normalized(self, env):
        """键转大写；no_proxy 并入 NO_PROXY（两者都有时取较长的那个：sh 只在其中一个上追加了原值）。"""
        result = {}
        for key in sorted(env):
            if key in self.SHELL_VARS:
                continue
            upper = key.upper()
            if upper == "NO_PROXY" and upper in result:
                if len(env[key]) > len(result[upper]):
                    result[upper] = env[key]
                continue
            result[upper] = env[key]
        return result

    def _sh_env(self, proxy, account_env, parent):
        script = launcher.render("a", self.acct, proxy, account_env, command_mode=True)
        path = self.write(os.path.join(self.tmp, "run.sh"), script)
        proc = subprocess.run(["/bin/sh", path, "env"], env=parent, stdout=subprocess.PIPE,
                              universal_newlines=True, check=True)
        result = {}
        for line in proc.stdout.splitlines():
            key, _, value = line.partition("=")
            result[key] = value
        return result

    def test_8_2_2_account_env_matches_sh(self):
        # 启动命令先检查账号目录存在，不存在时直接退出 1
        self.acct = os.path.join(self.tmp, "acct")
        os.makedirs(self.acct)
        parents = [{}, {"NO_PROXY": "corp"}, {"no_proxy": "corp"}]
        proxies = ["inherit", "http://127.0.0.1:7901", "socks5://127.0.0.1:7902", "off"]
        envs = [None, {"EXTRA": "1", "Foo": "1", "FOO": "2"}]
        for parent_extra in parents:
            parent = {"PATH": "/usr/bin:/bin", "CODEX_API_KEY": "leak", "HTTP_PROXY": "old", "all_proxy": "old"}
            parent.update(parent_extra)
            for proxy in proxies:
                for extra in envs:
                    with self.subTest(parent=parent_extra, proxy=proxy, env=extra):
                        expected = self._normalized(self._sh_env(proxy, extra, parent))
                        actual = self._normalized(launcher.account_env("a", self.acct, proxy, extra, base=parent))
                        self.assertEqual(expected, actual)

    def _in_process(self):
        patcher = mock.patch.dict(os.environ, self.env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("MULTI_CODEX_TEST_CRASH_AT", "MULTI_CODEX_TEST_FAIL_AT", "XDG_CONFIG_HOME", "CODEX_HOME"):
            os.environ.pop(name, None)

    def _migrate(self, copy):
        out, err = io.StringIO(), io.StringIO()
        denied = PermissionError(errno.EACCES, "denied")
        with redirect_stdout(out), redirect_stderr(err), mock.patch("os.rename", side_effect=denied):
            code = migrate.migrate_default("main", None, copy, False, None, True, False)
        return code, err.getvalue()

    def _source(self):
        source = os.path.join(self.home, ".codex")
        self.write(os.path.join(source, "auth.json"), "{}", 0o600)
        return source

    def test_8_2_6_rename_permission_error(self):
        self._in_process()
        source = self._source()
        code, err = self._migrate(copy=False)
        self.assertEqual(code, 1, err)
        self.assertNotIn("is in use by another program", err)
        self.assertTrue(os.path.isdir(source))
        self.assertIsNotNone(self.journal())

    def test_8_2_6_park_permission_error(self):
        self._in_process()
        source = self._source()
        code, err = self._migrate(copy=True)
        self.assertEqual(code, 1, err)
        self.assertNotIn("is in use by another program", err)
        self.assertTrue(os.path.isdir(source))
        self.assertFalse(os.path.exists(os.path.join(self.root, "main")))
        self.assertIsNone(self.journal())

    def test_8_2_6_restore_permission_error(self):
        self._in_process()
        self.ok("add", "a")
        self.write(os.path.join(self.root, "a", "auth.json"), "{}", 0o600)
        os.symlink(os.path.join(self.root, "a"), os.path.join(self.home, ".codex"))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), \
                mock.patch("os.rename", side_effect=PermissionError(errno.EACCES, "denied")):
            code = cli.main(["restore", "a", "--skip-process-check"])
        self.assertEqual(code, 1, err.getvalue())
        self.assertIn("move the new ~/.codex away", err.getvalue())
        self.assertNotIn("is in use by another program", err.getvalue())

    def test_8_2_launch_not_intercepted(self):
        result = self.run_cli("launch", "a", "--", "x")
        self.assertNotEqual(result.code, 0, result)
        self.assertNotIn("launch", completion.script("bash", cli.build_parser()).split())


if __name__ == "__main__":
    unittest.main()
