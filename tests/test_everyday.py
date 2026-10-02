"""feature-everyday-commands §8：set、--shared [DIR]、子命令拼写建议、list 简表与按 shell 的 PATH 提示。"""

import json
import os
import subprocess
import sys
import unittest

from helpers import ROOT, SRC, CliTestCase
from test_insight import chatgpt_claims, make_jwt, rate_limits, token_count_line

INSTALL = os.path.join(ROOT, "install.sh")

sys.path.insert(0, SRC)
from multi_codex import cli, completion, shellpath  # noqa: E402

ALL_ITEMS = "AGENTS.md, skills, rules, agents"


class EverydayBase(CliTestCase):
    def config(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle)

    def account_dir(self, name):
        return os.path.join(self.root, name)

    def write_logged_in(self, name):
        data = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                "tokens": {"id_token": make_jwt(chatgpt_claims()), "access_token": "x", "refresh_token": "x",
                           "account_id": "ws-1"}, "last_refresh": "2026-09-30T00:00:00Z"}
        self.write(os.path.join(self.account_dir(name), "auth.json"), json.dumps(data), 0o600)

    def fill_shared(self, directory, items=("AGENTS.md", "skills", "rules", "agents")):
        for item in items:
            if item.endswith(".md"):
                self.write(os.path.join(directory, item), "shared\n")
            else:
                self.write(os.path.join(directory, item, "keep.md"))


class SharedDefaultTest(EverydayBase):
    """T1、T2、T5：--shared 不带目录时的默认值与提示。"""

    def test_default_dir_when_not_set(self):
        default = os.path.join(self.home, ".codex-shared")
        self.fill_shared(default, ("skills",))
        result = self.ok("add", "work", "--shared")
        self.assertEqual(self.config()["shared"]["dir"], "~/.codex-shared")
        self.assertTrue(os.path.islink(os.path.join(self.account_dir("work"), "skills")))
        self.assertIn("shared directory is now ~/.codex-shared (was not set)", result.out)
        self.assertNotIn("has none of", result.out)

    def test_keeps_existing_dir(self):
        shared = os.path.join(self.tmp, "s")
        self.fill_shared(shared)
        self.ok("init", "--shared-dir", shared)
        result = self.ok("add", "work", "--shared")
        self.assertEqual(self.config()["shared"]["dir"], shared)
        self.assertNotIn("shared directory is now", result.out)

    def test_same_dir_other_spelling_is_not_a_change(self):
        self.fill_shared(os.path.join(self.home, "s"))
        self.ok("init", "--shared-dir", "~/s")
        self.ok("add", "work")
        result = self.ok("set", "work", "--shared", os.path.join(self.home, "s"))
        self.assertNotIn("shared directory is now", result.out)

    def test_empty_shared_dir_note(self):
        # 目录不存在
        result = self.ok("add", "a", "--shared")
        self.assertIn("has none of {} yet".format(ALL_ITEMS), result.out)
        # 目录存在但为空
        os.makedirs(os.path.join(self.home, ".codex-shared"))
        result = self.ok("add", "b", "--shared")
        self.assertIn("has none of {} yet".format(ALL_ITEMS), result.out)
        # 本次没有带 --shared：不重复提示
        result = self.ok("set", "a", "--proxy", "7901")
        self.assertNotIn("has none of", result.out)
        # 有一个条目就不提示
        self.fill_shared(os.path.join(self.home, ".codex-shared"), ("rules",))
        result = self.ok("add", "c", "--shared")
        self.assertNotIn("has none of", result.out)

    def test_dry_run_prints_no_hint(self):
        result = self.ok("add", "work", "--shared", "--dry-run")
        self.assertNotIn("shared directory is now", result.out)
        self.assertNotIn("has none of", result.out)
        self.assertFalse(os.path.exists(os.path.join(self.state, "config.json")))


class SharedDirTest(EverydayBase):
    """T3、T3b、T4、T4b：--shared DIR 修改全局共享目录。"""

    def setUp(self):
        super().setUp()
        self.old = os.path.join(self.tmp, "s1")
        self.new = os.path.join(self.tmp, "s2")
        self.fill_shared(self.old)
        self.ok("init", "--shared-dir", self.old)
        self.ok("add", "a", "--shared")
        self.ok("add", "b", "--shared")

    def link(self, name, item):
        return os.readlink(os.path.join(self.account_dir(name), item))

    def test_all_shared_accounts_follow(self):
        self.fill_shared(self.new)
        result = self.ok("set", "a", "--shared", self.new)
        self.assertEqual(self.config()["shared"]["dir"], self.new)
        for name in ("a", "b"):
            self.assertEqual(self.link(name, "skills"), os.path.join(self.new, "skills"))
        self.assertIn("shared directory is now", result.out)
        self.assertIn("every shared account follows it", result.out)

    def test_missing_item_is_unlinked(self):
        self.fill_shared(self.new, ("AGENTS.md", "rules", "agents"))
        result = self.ok("set", "a", "--shared", self.new)
        self.assertIn("delete shared-link", result.out)
        for name in ("a", "b"):
            self.assertFalse(os.path.lexists(os.path.join(self.account_dir(name), "skills")))
            self.assertEqual(self.link(name, "rules"), os.path.join(self.new, "rules"))

    def test_bad_values(self):
        before = self.snapshot()
        for args in (("add", "a", "--shared", "word"), ("add", "a", "--shared", "--no-shared")):
            result = self.run_cli(*args)
            self.assertEqual(result.code, 2, result)
        result = self.run_cli("add", "--shared", "work")
        self.assertEqual(result.code, 2, result)
        self.assertIn("required: name", result.err)
        self.assertEqual(self.snapshot(), before)

    def test_relative_dir_is_stored_absolute(self):
        self.fill_shared(self.new)
        self.ok("set", "a", "--shared", "./s2", cwd=self.tmp)
        self.assertEqual(self.config()["shared"]["dir"], self.new)

    def test_word_error_message(self):
        result = self.run_cli("set", "a", "--shared", "b")
        self.assertEqual(result.code, 2, result)
        self.assertIn("--shared DIR must be a path", result.err)


class SetTest(EverydayBase):
    """T6、T7：set 与 add 的结果相同；未登记与没有选项的报错。"""

    def test_same_as_add(self):
        shared = os.path.join(self.tmp, "s")
        self.fill_shared(shared)
        self.ok("init", "--shared-dir", shared)
        self.ok("add", "x")
        self.ok("add", "y")
        for verb, name in (("add", "x"), ("set", "y")):
            self.ok(verb, name, "--proxy", "7901")
            self.ok(verb, name, "--shared")
        accounts = self.config()["accounts"]
        self.assertEqual(accounts["x"]["proxy"], accounts["y"]["proxy"])
        self.assertEqual(accounts["x"]["managed_links"], accounts["y"]["managed_links"])
        self.ok("set", "y", "--no-shared")
        self.assertFalse(self.config()["accounts"]["y"]["shared"])
        self.assertFalse(os.path.lexists(os.path.join(self.account_dir("y"), "skills")))
        # --adopt：手工建的、指向共享条目的链接被接管
        os.symlink(os.path.join(shared, "skills"), os.path.join(self.account_dir("y"), "skills"))
        self.ok("set", "y", "--shared", "--adopt")
        self.assertIn("skills", self.config()["accounts"]["y"]["managed_links"])

    def test_errors(self):
        self.ok("add", "work")
        before = self.snapshot()
        result = self.run_cli("set", "nobody", "--proxy", "7901")
        self.assertEqual(result.code, 1, result)
        self.assertIn("use `multi-codex add NAME` to create it", result.err)
        result = self.run_cli("set", "wrk", "--proxy", "1")
        self.assertEqual(result.code, 1, result)
        self.assertIn("did you mean 'work'", result.err)
        self.assertNotIn("use `multi-codex add", result.err)
        result = self.run_cli("set", "work")
        self.assertEqual(result.code, 2, result)
        self.assertIn("nothing to set", result.err)
        result = self.run_cli("set", "nobody")
        self.assertEqual(result.code, 1, result)
        self.assertEqual(self.snapshot(), before)

    def test_no_accounts_yet(self):
        result = self.run_cli("set", "work", "--proxy", "1")
        self.assertEqual(result.code, 1, result)
        self.assertIn("run `multi-codex add NAME` first", result.err)
        self.assertNotIn("to create it", result.err)

    def test_set_gives_no_login_hint(self):
        self.ok("add", "work")
        result = self.ok("set", "work", "--proxy", "7901")
        self.assertNotIn("next: log in", result.err)


class UnknownCommandTest(EverydayBase):
    """T8：子命令拼写建议。"""

    def test_suggestions(self):
        result = self.run_cli("lsit")
        self.assertEqual(result.code, 2, result)
        self.assertIn("unknown command 'lsit'; did you mean 'list'?", result.err)
        result = self.run_cli("xyz")
        self.assertEqual(result.code, 2, result)
        self.assertIn("run multi-codex -h for the list", result.err)

    def test_unaffected(self):
        self.assertIn("usage: multi-codex", self.ok("-h").out)
        self.assertIn("multi-codex", self.ok("--version").out)
        self.ok("add", "work")
        # `--` 之后是要运行的命令，不参与子命令拼写判断（它本身是否运行成功与此无关）。
        result = self.run_cli("run", "work", "--", "lsit")
        self.assertNotIn("unknown command", result.err)


class ListSummaryTest(EverydayBase):
    """T9、T10、T11：list 简表与 --verbose。"""

    def rows(self, out):
        lines = out.splitlines()
        header = next(index for index, line in enumerate(lines) if line.startswith("NAME"))
        return lines[header].split(), lines[header + 1:]

    def test_status_column(self):
        self.ok("add", "good")
        self.write_logged_in("good")
        self.ok("add", "out")
        self.ok("add", "nolauncher")
        self.write_logged_in("nolauncher")
        self.ok("add", "gone")
        os.rmdir(self.account_dir("gone"))
        # 最后删：之后的任何写命令都会收敛全部账号，把启动命令重新建出来。
        os.unlink(os.path.join(self.bin, "codex-nolauncher"))
        result = self.ok("list")
        header, rows = self.rows(result.out)
        self.assertEqual(header, ["NAME", "LOGIN", "PROXY", "SHARED", "USAGE", "STATUS"])
        self.assertTrue(result.out.startswith("default: "))
        by_name = {row.split()[0]: row for row in rows if row and not row.startswith("run ")}
        self.assertTrue(by_name["good"].endswith(" ok"))
        self.assertTrue(by_name["out"].endswith("not logged in"))
        self.assertTrue(by_name["nolauncher"].endswith("launcher missing"), result.out)
        self.assertTrue(by_name["gone"].endswith("  missing-dir"))
        self.assertEqual(by_name["gone"].split()[1], "-")
        self.assertIn("run `multi-codex doctor` for details", result.out)

    def test_all_ok_has_no_doctor_line(self):
        self.ok("add", "good")
        self.write_logged_in("good")
        self.assertNotIn("multi-codex doctor", self.ok("list").out)

    def write_rollout(self, name, lines):
        path = os.path.join(self.account_dir(name), "sessions", "2026", "09", "30", "rollout-a.jsonl")
        self.write(path, "\n".join(lines) + "\n")

    def test_usage_column(self):
        for name in ("five", "two", "month", "reset", "none"):
            self.ok("add", name)
        self.write_rollout("five", [token_count_line(rate_limits(used=23.4))])
        weekly = {"used_percent": 41.0, "window_minutes": 10080, "resets_at": 4102444800}
        self.write_rollout("two", [token_count_line(rate_limits(used=23.0, secondary=weekly))])
        self.write_rollout("month", [token_count_line(rate_limits(used=7.0, minutes=43200))])
        self.write_rollout("reset", [token_count_line(rate_limits(used=50.0, resets_in=-60))])
        out = self.ok("list").out
        self.assertIn(" 5h 23% ", out)
        self.assertIn(" 5h 23%, 7d 41% ", out)
        self.assertIn(" 30d 7% ", out)
        self.assertIn(" 5h reset ", out)
        none_row = next(line for line in out.splitlines() if line.startswith("none "))
        self.assertEqual(none_row.split()[4], "-")
        self.assertNotIn("* sessions is shared", out)

    def test_prefers_codex_limit(self):
        self.ok("add", "work")
        self.write_rollout("work", [token_count_line(rate_limits(used=99.0, limit_id="other")),
                                    token_count_line(rate_limits(used=12.0))])
        out = self.ok("list").out
        self.assertIn(" 5h 12% ", out)

    def test_shared_sessions_marked(self):
        self.ok("add", "work")
        sessions = os.path.join(self.tmp, "shared-sessions")
        os.makedirs(sessions)
        os.symlink(sessions, os.path.join(self.account_dir("work"), "sessions"))
        self.write(os.path.join(sessions, "2026", "09", "30", "rollout-a.jsonl"), token_count_line(rate_limits(used=5.0)) + "\n")
        out = self.ok("list").out
        self.assertIn(" 5h 5%* ", out)
        self.assertIn("* sessions is shared with other accounts", out)

    def test_verbose_is_the_old_table(self):
        self.ok("add", "work", "--proxy", "7901")
        out = self.ok("list", "--verbose").out
        expected = (
            "root: ~/.cx\n"
            "bin_dir: ~/.local/bin\n"
            "shared.dir: (not set)\n"
            "default: (none)\n"
            "NAME  DIR  PROXY                  SHARED  LAUNCHER  LOGIN  PLAN\n"
            "work  ok   http://127.0.0.1:7901  no      ok        -      -\n")
        self.assertEqual(out, expected)

    def test_no_accounts(self):
        self.ok("init")
        self.assertEqual(self.ok("list").out, "default: (none)\nno accounts registered\n")


class ShellPathTest(unittest.TestCase):
    """T12：按 shell 的命令表。"""

    def test_table(self):
        directory = "/home/u/.local/bin"
        export = "echo 'export PATH=\"/home/u/.local/bin:$PATH\"' >> "
        self.assertEqual(shellpath.add_to_path_command(directory, "/bin/zsh", False), export + "~/.zshrc")
        self.assertEqual(shellpath.add_to_path_command(directory, "/bin/bash", True), export + "~/.bash_profile")
        self.assertEqual(shellpath.add_to_path_command(directory, "/usr/bin/bash", False), export + "~/.bashrc")
        self.assertEqual(shellpath.add_to_path_command(directory, "/usr/bin/fish", False),
                         "fish_add_path '/home/u/.local/bin'")
        self.assertIsNone(shellpath.add_to_path_command(directory, "/usr/bin/nushell", False))
        self.assertIsNone(shellpath.add_to_path_command(directory, None, False))
        for bad in ("/home/u/it's", '/home/u/a"b', "/home/u/$x", "/home/u/a`b", "/home/u/a\\b"):
            self.assertIsNone(shellpath.add_to_path_command(bad, "/bin/zsh", False), bad)

    def test_hint(self):
        self.assertEqual(shellpath.path_hint("/b", "/bin/zsh", False),
                         "run: echo 'export PATH=\"/b:$PATH\"' >> ~/.zshrc, then open a new terminal")
        self.assertEqual(shellpath.path_hint("/b", "/bin/tcsh", False), "add it to PATH in your shell profile")


class PathHintTest(EverydayBase):
    """T12b：add 之后、doctor、install.sh 三处给出同样格式的命令。"""

    def zsh_command(self, directory):
        return "echo 'export PATH=\"{}:$PATH\"' >> ~/.zshrc".format(directory)

    def test_add_and_doctor(self):
        env = {"SHELL": "/bin/zsh"}
        result = self.run_cli("add", "work", env=env)
        self.assertEqual(result.code, 0, result)
        self.assertIn("is not on PATH", result.err)
        self.assertIn("run: " + self.zsh_command(self.bin), result.err)
        report = json.loads(self.run_cli("doctor", "--json", env=env).out)
        check = next(item for item in report["checks"] if item["id"] == "path")
        self.assertIn(self.zsh_command(self.bin), check["hint"])
        # 没有 SHELL 时给通用说明
        report = json.loads(self.run_cli("doctor", "--json").out)
        check = next(item for item in report["checks"] if item["id"] == "path")
        self.assertEqual(check["hint"], "add it to PATH in your shell profile")

    def install(self, shell, uname, prefix=None):
        fake = os.path.join(self.tmp, "uname-bin")
        os.makedirs(fake, exist_ok=True)
        self.write(os.path.join(fake, "uname"), "#!/bin/sh\necho {}\n".format(uname), 0o755)
        env = dict(self.env)
        env.pop("PYTHONPATH")
        env["PATH"] = os.pathsep.join([fake, self.fakebin, os.environ.get("PATH", "/usr/bin:/bin")])
        env["SHELL"] = shell
        prefix = prefix or os.path.join(self.tmp, "prefix")
        result = subprocess.run(["sh", INSTALL, "--prefix", prefix], env=env, cwd=self.tmp,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_install(self):
        bin_dir = os.path.join(self.tmp, "prefix", "bin")
        for uname in ("Linux", "Darwin"):
            out = self.install("/bin/zsh", uname)
            self.assertIn("run: " + self.zsh_command(bin_dir) + ", then open a new terminal", out)
        out = self.install("/bin/bash", "Darwin")
        self.assertIn(">> ~/.bash_profile", out)
        out = self.install("/bin/bash", "Linux")
        self.assertIn(">> ~/.bashrc, then open", out)
        out = self.install("/usr/bin/fish", "Linux")
        self.assertIn("run: fish_add_path '{}'".format(bin_dir), out)
        out = self.install("/usr/local/bin/nushell", "Linux")
        self.assertIn("add it to PATH in your shell profile", out)
        # 相对 --prefix：BIN_DIR 不是绝对路径，写进 rc 文件会随当前目录变化，只给通用说明
        out = self.install("/bin/zsh", "Linux", prefix="rel-prefix")
        self.assertIn("add it to PATH in your shell profile", out)


class HelpAndCompletionTest(unittest.TestCase):
    """T14：帮助分组与补全。"""

    def test_help_group(self):
        everyday = dict(cli.COMMAND_GROUPS)["Everyday"]
        self.assertIn("set", [name for name, _ in everyday])

    def test_completion(self):
        spec = completion.command_spec(cli.build_parser())
        self.assertIn("set", spec)
        options, valued = spec["set"]
        for option in ("--proxy", "--shared", "--no-shared", "--adopt", "--config-from", "--dry-run"):
            self.assertIn(option, options)
        self.assertIn("set", completion.ACCOUNT_COMMANDS)
        list_options, _ = spec["list"]
        self.assertIn("--verbose", list_options)


if __name__ == "__main__":
    unittest.main()
