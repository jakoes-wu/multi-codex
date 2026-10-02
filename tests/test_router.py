"""feature-auto-launcher §8：codex-auto 入口与 which。"""

import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import SRC, CliTestCase
from test_binding import case_insensitive

sys.path.insert(0, SRC)
from multi_codex import cli, completion, router  # noqa: E402


class RouterBase(CliTestCase):
    def setUp(self):
        super().setUp()
        self.auto = os.path.join(self.bin, "codex-auto")
        self.projects = os.path.join(self.tmp, "p")
        os.makedirs(self.projects)

    def project(self, *parts):
        path = os.path.join(self.projects, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def config(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle)

    def write_config(self, data):
        with open(os.path.join(self.state, "config.json"), "w") as handle:
            json.dump(data, handle)

    def run_auto(self, cwd, *args, env=None):
        """在 cwd 运行 codex-auto，返回 (退出码, stderr, 假 codex 看到的环境或 None)。"""
        full_env = dict(self.env)
        full_env.update(env or {})
        for suffix in (".args", ".env"):
            if os.path.exists(self.fake_out + suffix):
                os.unlink(self.fake_out + suffix)
        proc = subprocess.run([self.auto] + list(args), cwd=cwd, env=full_env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        seen = None
        if os.path.exists(self.fake_out + ".env"):
            seen = {}
            with open(self.fake_out + ".env") as handle:
                for line in handle.read().splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        seen[key] = value
        return proc.returncode, proc.stderr, seen

    def account_of(self, seen):
        """从假 codex 看到的 CODEX_HOME 反推账号名；没有 CODEX_HOME 表示普通 codex。"""
        if seen is None or "CODEX_HOME" not in seen:
            return None
        return os.path.basename(seen["CODEX_HOME"])

    def run_choice(self, cwd):
        """`run` 省略账号名时选中的账号，或 None（没有绑定 / 报错）。"""
        result = self.run_cli("run", "--", "codex", cwd=cwd)
        if result.code != 0:
            return None
        with open(self.fake_out + ".env") as handle:
            for line in handle.read().splitlines():
                if line.startswith("CODEX_HOME="):
                    return os.path.basename(line.split("=", 1)[1])
        return None


class GenerateTest(RouterBase):
    """T1、T2、T9、T12、T13、T14。"""

    def test_lifecycle(self):
        self.ok("add", "work")
        self.assertFalse(os.path.lexists(self.auto))
        result = self.ok("bind", "work", self.project("a"))
        self.assertIn("create launcher ~/.local/bin/codex-auto", result.out)
        self.assertTrue(router.is_router(self.auto))
        self.assertEqual(os.stat(self.auto).st_mode & 0o777, 0o755)
        self.assertIn("already up to date", self.ok("apply").out)
        result = self.ok("unbind", self.project("a"))
        self.assertIn("no directory bindings", result.out)
        self.assertFalse(os.path.lexists(self.auto))

    def test_bin_dir_change(self):
        self.ok("add", "work")
        self.ok("bind", "work", self.project("a"))
        new_bin = os.path.join(self.home, "bin2")
        self.ok("init", "--bin-dir", new_bin)
        self.assertFalse(os.path.lexists(self.auto))
        self.assertTrue(router.is_router(os.path.join(new_bin, "codex-auto")))

    def test_doctor_drift(self):
        self.ok("add", "work")
        self.ok("bind", "work", self.project("a"))
        os.unlink(self.auto)
        report = json.loads(self.run_cli("doctor", "--json").out)
        drift = next(item for item in report["checks"] if item["id"] == "drift")
        self.assertNotEqual(drift["status"], "ok")
        self.ok("apply")
        report = json.loads(self.run_cli("doctor", "--json").out)
        drift = next(item for item in report["checks"] if item["id"] == "drift")
        self.assertEqual(drift["status"], "ok")

    def test_user_file_is_a_conflict(self):
        self.ok("add", "work")
        self.write(self.auto, "#!/bin/sh\necho mine\n", 0o755)
        before = self.snapshot()
        result = self.run_cli("bind", "work", self.project("a"))
        self.assertEqual(result.code, 3, result)
        self.assertIn("not the multi-codex router", result.err)
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck is not installed")
    def test_shellcheck(self):
        self.ok("add", "work")
        self.ok("add", "home")
        self.ok("bind", "work", self.project("a"))
        self.ok("bind", "home", self.project("a", "sub dir"))
        self.ok("bind", "home", "/")
        data = self.config()
        data["bindings"][self.project("gone")] = "gone"
        self.write_config(data)
        self.ok("apply")
        proc = subprocess.run(["shellcheck", self.auto], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)


class MatchTest(RouterBase):
    """T3–T8：选账号的规则与 run 一致。"""

    def setUp(self):
        super().setUp()
        self.ok("add", "work")
        self.ok("add", "home")

    def test_choose_like_run(self):
        top = self.project("a")
        sub = self.project("a", "sub")
        self.ok("bind", "work", top)
        self.ok("bind", "home", sub)
        cases = ((top, "work"), (self.project("a", "other"), "work"), (sub, "home"),
                 (self.project("a", "sub", "deeper"), "home"))
        for cwd, expected in cases:
            with self.subTest(cwd=cwd):
                code, _, seen = self.run_auto(cwd, "x", env={"CODEX_API_KEY": "leak"})
                self.assertEqual(code, 0)
                self.assertEqual(self.account_of(seen), expected)
                self.assertNotIn("CODEX_API_KEY", seen)  # 经账号启动命令，隔离变量被清除
                self.assertEqual(self.run_choice(cwd), expected)

    def test_unbound_runs_plain_codex(self):
        self.ok("bind", "work", self.project("a"))
        code, err, seen = self.run_auto(self.project("b"), "x")
        self.assertEqual(code, 0)
        self.assertIsNone(self.account_of(seen))
        self.assertIn("no account is bound to", err)
        self.assertIn("default account", err)

    def test_special_names(self):
        names = ("with space", "it's", "中文", "a*", "a?b", "[x]")
        for name in names:
            self.ok("bind", "work", self.project(name))
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(self.account_of(self.run_auto(self.project(name))[2]), "work")
        for sibling in ("ab", "axb", "x"):
            with self.subTest(sibling=sibling):
                self.assertIsNone(self.account_of(self.run_auto(self.project(sibling))[2]))

    def test_root_binding(self):
        self.ok("bind", "home", "/")
        self.ok("bind", "work", self.project("a"))
        self.assertEqual(self.account_of(self.run_auto(self.project("a", "x"))[2]), "work")
        self.assertEqual(self.account_of(self.run_auto(self.project("b"))[2]), "home")

    def test_symlink(self):
        real = self.project("real")
        self.ok("bind", "work", real)
        link = os.path.join(self.tmp, "lnk")
        os.symlink(real, link)
        self.assertEqual(self.account_of(self.run_auto(link)[2]), "work")

    def test_case_insensitive_cd(self):
        if not case_insensitive(self.projects):
            self.skipTest("the file system is case-sensitive")
        real = self.project("CaseDir")
        self.ok("bind", "work", real)
        typed = os.path.join(self.projects, "casedir")
        # 用 /bin/sh -c 'cd 小写写法 && codex-auto'，模拟用户在 shell 里换了大小写 cd 进来。
        proc = subprocess.run(["/bin/sh", "-c", 'cd "$1" && exec "$2"', "sh", typed, self.auto], env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(self.fake_out + ".env") as handle:
            self.assertIn("CODEX_HOME=" + os.path.join(self.root, "work"), handle.read())

    def test_stale_binding(self):
        self.ok("bind", "work", self.project("a"))
        data = self.config()
        data["bindings"][os.path.realpath(self.project("a", "sub"))] = "gone"
        self.write_config(data)
        self.ok("apply")
        code, err, seen = self.run_auto(self.project("a", "sub"))
        self.assertEqual(code, 1)
        self.assertIn("is not registered", err)
        self.assertIn("multi-codex unbind", err)
        self.assertIsNone(seen)
        self.assertEqual(self.run_cli("run", "--", "codex", cwd=self.project("a", "sub")).code, 1)

    def test_missing_launcher(self):
        self.ok("bind", "work", self.project("a"))
        os.unlink(os.path.join(self.bin, "codex-work"))
        code, err, _ = self.run_auto(self.project("a"))
        self.assertEqual(code, 1)
        self.assertIn("is missing", err)


class AutoAccountTest(RouterBase):
    """T10–T10f：名为 auto 的账号与入口。"""

    def setUp(self):
        super().setUp()
        self.ok("add", "work")

    def test_conflicts(self):
        self.ok("add", "auto")
        before = self.snapshot()
        result = self.run_cli("bind", "work", self.project("a"))
        self.assertEqual(result.code, 3, result)
        self.assertIn("multi-codex rename auto", result.err)
        self.assertEqual(self.snapshot(), before)
        self.ok("remove", "auto")
        self.ok("bind", "work", self.project("a"))
        before = self.snapshot()
        self.assertEqual(self.run_cli("add", "auto").code, 3)
        self.assertEqual(self.snapshot(), before)

    def auto_with_binding(self, name="auto"):
        """手写配置造出“账号 auto + 绑定”：正常命令不允许这种组合。"""
        self.ok("add", name)
        data = self.config()
        data.setdefault("bindings", {})[os.path.realpath(self.project("a"))] = "work"
        self.write_config(data)

    def test_rename_remove_restore_auto(self):
        for command in (("rename", "auto", "x"), ("remove", "auto")):
            with self.subTest(command=command):
                self.auto_with_binding()
                self.ok(*command)
                self.assertTrue(router.is_router(self.auto))
                self.assertEqual(self.account_of(self.run_auto(self.project("a"))[2]), "work")
                # 复位：删掉绑定、清理改名后的账号
                self.ok("unbind", self.project("a"))
                if command[0] == "rename":
                    self.ok("remove", "x")
        self.auto_with_binding()
        self.ok("use", "auto")
        self.ok("restore", "auto")
        self.assertTrue(router.is_router(self.auto))

    def test_restore_other_account_is_refused_before_moving(self):
        # 绑定指向第三个账号 home：restore work 之后仍有绑定，名为 auto 的账号与入口冲突。
        self.ok("add", "home")
        self.ok("add", "auto")
        data = self.config()
        data.setdefault("bindings", {})[os.path.realpath(self.project("a"))] = "home"
        self.write_config(data)
        self.ok("use", "work")
        link = os.path.join(self.home, ".codex")
        result = self.run_cli("restore", "work")
        self.assertEqual(result.code, 3, result)
        self.assertIn("nothing was moved", result.err)
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "work")))
        self.assertFalse(os.path.exists(os.path.join(self.state, "restore-journal.json")))

    def test_case_variant_on_case_insensitive_fs(self):
        if not case_insensitive(self.bin if os.path.isdir(self.bin) else self.tmp):
            self.skipTest("the file system is case-sensitive")
        self.auto_with_binding("Auto")
        self.ok("rename", "Auto", "x")
        self.assertTrue(router.is_router(self.auto))


class FollowTest(RouterBase):
    """T10d、T10e：rename、remove、apply -f、add 让入口跟着变。"""

    def setUp(self):
        super().setUp()
        self.ok("add", "work")
        self.ok("bind", "work", self.project("a"))

    def test_rename_updates_target(self):
        result = self.ok("rename", "work", "job")
        self.assertIn("update launcher ~/.local/bin/codex-auto", result.out)
        with open(self.auto) as handle:
            self.assertIn("codex-job", handle.read())
        self.assertEqual(self.account_of(self.run_auto(self.project("a"))[2]), "work")  # 目录名仍是 work
        self.assertEqual(self.run_choice(self.project("a")), "work")

    def test_remove_and_apply_file(self):
        self.ok("remove", "work")
        self.assertFalse(os.path.lexists(self.auto))
        self.ok("add", "work")
        self.ok("bind", "work", self.project("a"))
        data = self.config()
        del data["accounts"]["work"]
        path = os.path.join(self.tmp, "accounts.json")
        self.write(path, json.dumps(data))
        self.ok("apply", "-f", path)
        self.assertFalse(os.path.lexists(self.auto))

    def test_add_makes_stale_binding_valid(self):
        data = self.config()
        data["bindings"][os.path.realpath(self.project("b"))] = "home"
        self.write_config(data)
        self.ok("apply")
        self.assertEqual(self.run_auto(self.project("b"))[0], 1)
        self.ok("add", "home")
        self.assertEqual(self.account_of(self.run_auto(self.project("b"))[2]), "home")


class WhichTest(RouterBase):
    """T15。"""

    def setUp(self):
        super().setUp()
        self.ok("add", "work")
        self.ok("bind", "work", self.project("a"))

    def test_bound(self):
        result = self.ok("which", self.project("a", "deep"))
        self.assertEqual(result.out, "work\n")
        self.assertIn("bound to", result.err)
        result = self.ok("which", cwd=self.project("a"))
        self.assertEqual(result.out, "work\n")

    def test_unbound(self):
        result = self.ok("which", self.project("b"))
        self.assertEqual(result.out, self.ok("use").out)
        self.assertIn("no binding for", result.err)
        result = self.ok("which", self.project("b"), env={"CODEX_HOME": "/somewhere"})
        self.assertIn("CODEX_HOME is set", result.err)

    def test_errors(self):
        data = self.config()
        data["bindings"][os.path.realpath(self.project("c"))] = "gone"
        self.write_config(data)
        result = self.run_cli("which", self.project("c"))
        self.assertEqual(result.code, 1, result)
        self.assertIn("is not registered", result.err)
        self.assertEqual(self.run_cli("which", os.path.join(self.tmp, "nope")).code, 1)


class HelpTest(unittest.TestCase):
    """T17。"""

    def test_help_and_completion(self):
        everyday = dict(cli.COMMAND_GROUPS)["Everyday"]
        self.assertIn("which", [name for name, _ in everyday])
        self.assertIn("which", completion.command_spec(cli.build_parser()))


if __name__ == "__main__":
    unittest.main()
