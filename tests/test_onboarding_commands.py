"""feature-onboarding-commands §8：login 子命令、migrate-default 省略名称、写命令默认只打印变化。"""

import json
import os
import unittest

from helpers import CliTestCase
from test_insight import chatgpt_claims, make_jwt

CRASH = 137


class CommandsBase(CliTestCase):
    def write_auth(self, directory, data):
        self.write(os.path.join(directory, "auth.json"), json.dumps(data), 0o600)

    def chatgpt_auth(self, email):
        return {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                "tokens": {"id_token": make_jwt(chatgpt_claims(email=email)), "access_token": "x",
                           "refresh_token": "x", "account_id": "ws-1"}}

    @property
    def source(self):
        return os.path.join(self.home, ".codex")


class LoginTest(CommandsBase):
    """§8 第 1–8 条。"""

    def test_login_runs_codex_login_with_account_env(self):
        self.ok("add", "work")
        # PATH 中没有启动命令目录（helpers 默认不含），login 照样能用。
        self.ok("login", "work")
        with open(self.fake_out + ".args") as handle:
            self.assertEqual(handle.read().split("\n")[:-1], ["login"])
        with open(self.fake_out + ".env") as handle:
            self.assertIn("CODEX_HOME={}".format(os.path.join(self.root, "work")), handle.read().splitlines())

    def test_arguments_after_double_dash(self):
        self.ok("add", "work")
        self.ok("login", "work", "--", "--device-auth")
        with open(self.fake_out + ".args") as handle:
            self.assertEqual(handle.read().split("\n")[:-1], ["login", "--device-auth"])

    def test_unknown_account(self):
        self.ok("add", "work")
        result = self.run_cli("login", "wrk")
        self.assertEqual(result.code, 1)
        self.assertIn("did you mean 'work'?", result.err)

    def test_help_lists_login(self):
        out = self.ok("-h").out
        self.assertLess(out.index("Get started:"), out.index("  login"))
        self.assertLess(out.index("  login"), out.index("Everyday:"))

    def test_completion(self):
        for shell, marker in (("bash", '"$cmd" == login'), ("zsh", '"$cmd" == login'), ("fish", "run login")):
            script = self.ok("completion", shell).out
            self.assertIn("login", script, shell)
            self.assertIn(marker, script, shell)

    def test_add_hint_uses_login(self):
        result = self.ok("add", "work")
        self.assertIn("multi-codex login work", result.err)
        on_path = self.ok("add", "other", env={"PATH": os.pathsep.join([self.bin, self.env["PATH"]])})
        self.assertIn("multi-codex login other", on_path.err)

    def test_doctor_hint(self):
        self.ok("add", "work")
        report = json.loads(self.run_cli("doctor", "--json").out)
        check = next(item for item in report["checks"] if item["id"] == "account:work")
        self.assertEqual(check["hint"], "multi-codex login work")

    def test_getting_started(self):
        self.assertIn("multi-codex login NAME", self.ok().out)


class MigrateNameTest(CommandsBase):
    """§8 第 9–12 条。"""

    def test_name_from_email(self):
        self.write_auth(self.source, self.chatgpt_auth("me@example.com"))
        result = self.ok("migrate-default")
        self.assertIn("using account name 'me@example.com'", result.out)
        self.assertTrue(os.path.islink(self.source))
        self.assertEqual(os.path.realpath(self.source), os.path.realpath(os.path.join(self.root, "me@example.com")))

    def assert_refused(self, reason):
        before = self.snapshot(self.source)
        result = self.run_cli("migrate-default")
        self.assertEqual(result.code, 2, result)
        self.assertIn("pass a NAME", result.err)
        self.assertIn(reason, result.err)
        self.assertEqual(before, self.snapshot(self.source))

    def test_not_logged_in(self):
        os.makedirs(self.source)
        self.assert_refused("not logged in")

    def test_api_key(self):
        self.write_auth(self.source, {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"})
        self.assert_refused("API key")

    def test_keyring(self):
        self.write(os.path.join(self.source, "config.toml"), 'cli_auth_credentials_store = "keyring"\n')
        self.assert_refused("system keyring")

    def test_invalid_email(self):
        self.write_auth(self.source, self.chatgpt_auth("a b@example.com"))
        self.assert_refused("is not a valid account name")

    def test_dry_run(self):
        self.write_auth(self.source, self.chatgpt_auth("me@example.com"))
        before = self.snapshot(self.source)
        result = self.ok("migrate-default", "--dry-run")
        self.assertIn("using account name 'me@example.com'", result.out)
        self.assertIn("(dry-run)", result.out)
        self.assertEqual(before, self.snapshot(self.source))

    def test_resume_uses_journal_name(self):
        self.write_auth(self.source, self.chatgpt_auth("me@example.com"))
        crashed = self.run_cli("migrate-default", "main", "--copy",
                               env={"MULTI_CODEX_TEST_CRASH_AT": "journal-moved"})
        self.assertEqual(crashed.code, CRASH, crashed)
        self.ok("migrate-default")
        self.assertIsNone(self.journal())
        self.assertEqual(os.path.realpath(self.source), os.path.realpath(os.path.join(self.root, "main")))


class QuietOutputTest(CommandsBase):
    """§8 第 13–17 条。"""

    def test_repeat_add(self):
        first = self.ok("add", "work").out
        self.assertEqual(first.count(" create "), 3)
        self.assertNotIn("unchanged", first)
        second = self.ok("add", "work").out
        self.assertEqual(second.strip(), "[multi-codex] already up to date")
        self.assertEqual(self.ok("add", "work", "-v").out.count(" unchanged "), 3)

    def test_skip_lines_are_kept(self):
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(shared)
        self.ok("init", "--shared-dir", shared)
        self.ok("add", "work")
        out = self.ok("add", "work", "--shared").out
        self.assertIn(" skip shared-link ", out)
        self.assertNotIn("already up to date", out)

    def test_dry_run(self):
        self.ok("add", "work")
        self.assertEqual(self.ok("add", "work", "--dry-run").out.strip(), "[multi-codex] already up to date")
        self.assertIn("create (dry-run) launcher", self.ok("add", "new", "--dry-run").out)

    def test_repeat_bind(self):
        self.ok("add", "work")
        project = os.path.join(self.tmp, "project")
        os.makedirs(project)
        self.assertIn("create binding", self.ok("bind", "work", project).out)
        self.assertEqual(self.ok("bind", "work", project).out.strip(), "[multi-codex] already up to date")
        self.assertIn("unchanged binding", self.ok("bind", "work", project, "-v").out)

    def test_conflict_is_still_printed(self):
        self.write(os.path.join(self.bin, "codex-work"), "#!/bin/sh\necho mine\n", 0o755)
        result = self.run_cli("add", "work")
        self.assertEqual(result.code, 3, result)
        self.assertIn("conflict launcher", result.err)
        self.assertNotIn("already up to date", result.out)


if __name__ == "__main__":
    unittest.main()
