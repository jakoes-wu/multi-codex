"""feature-onboarding-hints §8：下一步提示、上手指引、分组帮助、拼写提示、报错改进、路径缩写与安装收尾提示。"""

import json
import os
import subprocess
import sys
import unittest

from helpers import ROOT, SRC, CliTestCase
from test_insight import chatgpt_claims, make_jwt

INSTALL = os.path.join(ROOT, "install.sh")

sys.path.insert(0, SRC)
from multi_codex import cli, completion  # noqa: E402


class OnboardingBase(CliTestCase):
    def with_bin_on_path(self):
        return {"PATH": os.pathsep.join([self.bin, self.env["PATH"]])}

    def write_logged_in(self, name):
        data = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                "tokens": {"id_token": make_jwt(chatgpt_claims()), "access_token": "x", "refresh_token": "x",
                           "account_id": "ws-1"}, "last_refresh": "2026-09-30T00:00:00Z"}
        self.write(os.path.join(self.root, name, "auth.json"), json.dumps(data), 0o600)


class AddHintTest(OnboardingBase):
    """§8 第 1–4 条：add 之后的登录提示与 PATH 警告。"""

    def test_bin_dir_not_on_path(self):
        result = self.ok("add", "work")
        self.assertIn("multi-codex run work -- codex login", result.err)
        self.assertIn("is not on PATH", result.err)
        self.assertNotIn("codex-work login", result.err)

    def test_bin_dir_on_path(self):
        result = self.ok("add", "work", env=self.with_bin_on_path())
        self.assertIn("hint: next: log in with `codex-work login`", result.err)
        self.assertNotIn("is not on PATH", result.err)

    def test_logged_in_account_gets_no_login_hint(self):
        self.ok("add", "work")
        self.write_logged_in("work")
        result = self.ok("add", "work", env=self.with_bin_on_path())
        self.assertNotIn("log in with", result.err)

    def test_dry_run_has_no_hint(self):
        result = self.ok("add", "work", "--dry-run")
        self.assertNotIn("hint:", result.err)
        self.assertNotIn("is not on PATH", result.err)

    def test_hint_follows_action_lines(self):
        # stdout 与 stderr 合并到同一管道时，提示应排在 create 行之后。
        full_env = dict(self.env)
        proc = subprocess.run([sys.executable, "-m", "multi_codex", "add", "work"], env=full_env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              cwd=self.tmp)
        lines = proc.stdout.splitlines()
        self.assertLess(max(i for i, line in enumerate(lines) if " create " in line),
                        min(i for i, line in enumerate(lines) if "hint:" in line))


class MigrateHintTest(OnboardingBase):
    """§8 第 5 条：migrate-default 之后只提醒 PATH，不提示登录。"""

    def test_migrate_warns_path_only(self):
        self.write(os.path.join(self.home, ".codex", "config.toml"), "")
        result = self.ok("migrate-default", "main")
        self.assertIn("is not on PATH", result.err)
        self.assertNotIn("log in with", result.err)


class GettingStartedTest(OnboardingBase):
    """§8 第 6 条：不带参数运行。"""

    def test_no_config(self):
        result = self.ok()
        self.assertIn("multi-codex add NAME", result.out)
        self.assertNotIn("migrate-default", result.out)
        self.assertIn("multi-codex -h", result.out)

    def test_existing_codex_dir(self):
        os.makedirs(os.path.join(self.home, ".codex"))
        self.assertIn("multi-codex migrate-default NAME", self.ok().out)

    def test_with_accounts(self):
        self.ok("add", "work")
        result = self.ok()
        self.assertIn("Accounts: work", result.out)
        self.assertNotIn("Get started", result.out)

    def test_broken_config_still_exits_zero(self):
        self.write(os.path.join(self.state, "config.json"), "{not json")
        result = self.ok()
        self.assertIn("multi-codex add NAME", result.out)


class HelpTest(OnboardingBase):
    """§8 第 7–8 条：分组帮助与补全。"""

    def registered_commands(self):
        parser = cli.build_parser()
        return set(completion.command_spec(parser))

    def test_groups_in_order(self):
        out = self.ok("-h").out
        positions = [out.index(title + ":") for title in ("Get started", "Everyday", "Advanced")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("examples:", out)

    def test_groups_cover_every_command(self):
        grouped = [name for _, commands in cli.COMMAND_GROUPS for name, _ in commands]
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(set(grouped), self.registered_commands())

    def test_subcommand_help_keeps_summary(self):
        self.assertIn(cli.COMMAND_SUMMARY["add"], self.ok("add", "-h").out)

    def test_completion_lists_every_command_and_option(self):
        spec = completion.command_spec(cli.build_parser())
        for shell in completion.SHELLS:
            script = self.ok("completion", shell).out
            for name, (options, _) in spec.items():
                self.assertIn(name, script, shell)
                for option in options:
                    # fish 用 `-l name` / `-s x` 声明选项，bash、zsh 直接写出选项原文。
                    if shell == "fish":
                        option = ("-l " + option[2:]) if option.startswith("--") else ("-s " + option[1:])
                    self.assertIn(option, script, (shell, name))


class SpellingHintTest(OnboardingBase):
    """§8 第 9 条：账号名拼错时的提示。"""

    def test_close_match(self):
        self.ok("add", "work")
        for args in (("run", "wrk"), ("path", "wrk"), ("env", "wrk"), ("proxy", "wrk", "off")):
            result = self.run_cli(*args)
            self.assertNotEqual(result.code, 0, args)
            self.assertIn("did you mean 'work'?", result.err, args)

    def test_lists_registered(self):
        self.ok("add", "work")
        result = self.run_cli("path", "zzz")
        self.assertEqual(result.code, 1)
        self.assertIn("registered: work", result.err)

    def test_no_accounts(self):
        result = self.run_cli("path", "x")
        self.assertEqual(result.code, 1)
        self.assertIn("multi-codex add NAME", result.err)

    def test_use_and_restore(self):
        self.ok("add", "work")
        for command in ("use", "restore"):
            result = self.run_cli(command, "wrk")
            self.assertNotEqual(result.code, 0, command)
            self.assertIn("did you mean 'work'?", result.err, command)

    def test_usage(self):
        self.ok("add", "work")
        result = self.run_cli("usage", "wrk")
        self.assertEqual(result.code, 1)
        self.assertIn("did you mean 'work'?", result.out)


class DoctorPathTest(OnboardingBase):
    """§8 第 10 条：doctor 的 path 检查输出不变。"""

    def check(self, env=None):
        self.ok("add", "work")
        report = json.loads(self.run_cli("doctor", "--json", env=env).out)
        return next(item for item in report["checks"] if item["id"] == "path")

    def test_missing(self):
        item = self.check()
        self.assertEqual(item["status"], "warn")
        self.assertEqual(item["message"], "{} is not in PATH; codex-<name> launchers cannot be found".format(self.bin))

    def test_present(self):
        item = self.check(env=self.with_bin_on_path())
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["message"], "{} is in PATH".format(self.bin))


class ErrorMessageTest(OnboardingBase):
    """§8 第 11–13 条：proxy、remove、use 的报错与提示。"""

    def test_proxy_values(self):
        self.ok("add", "work")
        result = self.run_cli("proxy", "work", "abc")
        self.assertEqual(result.code, 2)
        self.assertIn("use a port number", result.err)
        result = self.run_cli("proxy", "work", "ftp://h:1")
        self.assertEqual(result.code, 2)
        self.assertIn("unsupported proxy scheme", result.err)
        self.ok("proxy", "work", "7901")

    def test_remove_unknown_still_cleans_leftover_launcher(self):
        self.ok("add", "nope")
        config_path = os.path.join(self.state, "config.json")
        with open(config_path) as handle:
            data = json.load(handle)
        del data["accounts"]["nope"]
        with open(config_path, "w") as handle:
            json.dump(data, handle)
        result = self.ok("remove", "nope")
        self.assertIn("nothing to remove", result.out)
        self.assertFalse(os.path.lexists(os.path.join(self.bin, "codex-nope")))

    def test_use_show_hints(self):
        result = self.ok("use")
        self.assertEqual(result.out.strip(), "(none)")
        self.assertIn("multi-codex use NAME", result.err)
        os.makedirs(os.path.join(self.home, ".codex"))
        self.assertIn("migrate-default", self.ok("use").err)


class DisplayPathTest(OnboardingBase):
    """§8 第 14 条：只有给人看的输出缩写路径。"""

    def test_paths(self):
        out = self.ok("add", "work").out
        self.assertIn("~/.local/bin/codex-work", out)
        self.assertNotIn(self.home, out)
        self.assertIn("root: ~/.cx", self.ok("list").out)
        self.assertEqual(self.ok("path", "work").out.strip(), os.path.join(self.root, "work"))
        self.assertEqual(json.loads(self.ok("list", "--json").out)["root"], self.root)


class InstallHintTest(CliTestCase):
    """§8 第 15 条：install.sh 结束时的下一步与补全提示。"""

    def setUp(self):
        super().setUp()
        self.prefix = os.path.join(self.tmp, "prefix")
        self.env.pop("PYTHONPATH")
        self.env["PATH"] = self.fakebin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")

    def install(self, *args, shell):
        env = dict(self.env)
        # SHELL 必须显式给值：未设置时 bash（macOS 的 /bin/sh）会从用户数据库补上登录 shell。
        env["SHELL"] = shell
        return subprocess.run(["sh", INSTALL, "--prefix", self.prefix] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def test_next_step_and_completion(self):
        result = self.install(shell="/bin/zsh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("next: multi-codex add NAME", result.stdout)
        self.assertIn("completion zsh", result.stdout)

    def test_unknown_shell_has_no_completion_hint(self):
        result = self.install(shell="/usr/local/bin/nushell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("tab completion", result.stdout)

    def test_config_skips_next_step(self):
        config = self.write(os.path.join(self.tmp, "accounts.json"), json.dumps(
            {"version": 1, "accounts": {"work": {"proxy": "inherit"}}}))
        result = self.install("--config", config, shell="/bin/bash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("next:", result.stdout)
        self.assertIn("completion bash", result.stdout)


if __name__ == "__main__":
    unittest.main()
