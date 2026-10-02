"""feature-isolation-sharing-rename §8：启动命令清除隔离变量、禁止共享清单、按账号退出共享项与 rename。"""

import json
import os
import sys
import unittest

from helpers import SRC, CliTestCase

sys.path.insert(0, SRC)
from multi_codex import apps, cli, completion, config as config_module, launcher  # noqa: E402

ISOLATION = ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_SQLITE_HOME")
SHELL_ISOLATION = {"CODEX_API_KEY": "shell-key", "CODEX_ACCESS_TOKEN": "shell-token", "CODEX_SQLITE_HOME": "/tmp/x"}
CRASH = 137


class IsolationBase(CliTestCase):
    def config(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle)

    def write_config(self, data):
        with open(os.path.join(self.state, "config.json"), "w") as handle:
            json.dump(data, handle)

    def account_dir(self, name):
        return os.path.join(self.root, name)

    def launcher_file(self, name):
        return os.path.join(self.bin, "codex-" + name)

    def recorded_env(self):
        env = {}
        with open(self.fake_out + ".env") as handle:
            for line in handle.read().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    env[key] = value
        return env

    def setup_shared(self, items=("AGENTS.md", "skills", "rules")):
        self.shared = os.path.join(self.tmp, "s")
        for item in items:
            if item.endswith(".md"):
                self.write(os.path.join(self.shared, item), "shared\n")
            else:
                self.write(os.path.join(self.shared, item, "keep.md"))
        self.ok("init", "--shared-dir", self.shared, "--shared-items", ",".join(items))


class LauncherEnvTest(IsolationBase):
    """T1–T4b：启动命令、run、login 清除隔离变量；账号 env 仍生效；升级后的 stale 状态。"""

    def setUp(self):
        super().setUp()
        self.ok("add", "work")

    def test_launcher_clears_shell_values(self):
        code, _, env, _ = self.run_launcher("work", env=SHELL_ISOLATION)
        self.assertEqual(code, 0)
        for key in ISOLATION:
            self.assertNotIn(key, env)
        self.assertEqual(env["CODEX_HOME"], self.account_dir("work"))

    def test_account_env_wins(self):
        self.ok("env", "work", "CODEX_API_KEY=k")
        _, _, env, _ = self.run_launcher("work", env=SHELL_ISOLATION)
        self.assertEqual(env["CODEX_API_KEY"], "k")
        self.assertNotIn("CODEX_ACCESS_TOKEN", env)
        self.assertNotIn("CODEX_SQLITE_HOME", env)

    def test_run_and_login(self):
        self.ok("env", "work", "CODEX_API_KEY=k")
        for args in (("run", "work", "--", "codex", "x"), ("login", "work")):
            with self.subTest(args=args):
                result = self.run_cli(*args, env=SHELL_ISOLATION)
                self.assertEqual(result.code, 0, result)
                env = self.recorded_env()
                self.assertEqual(env["CODEX_API_KEY"], "k")
                self.assertNotIn("CODEX_ACCESS_TOKEN", env)
                self.assertNotIn("CODEX_SQLITE_HOME", env)

    def old_launcher(self):
        # 0.8 的启动命令：没有 unset 行。
        content = launcher.render("work", self.account_dir("work"), "inherit")
        return content.replace("unset CODEX_ACCESS_TOKEN CODEX_API_KEY CODEX_SQLITE_HOME\n", "")

    def test_upgrade_marks_launchers_stale(self):
        self.write(self.launcher_file("work"), self.old_launcher(), 0o755)
        self.assertIn("launcher stale", self.ok("list").out)
        result = self.run_cli("usage", "--live", "--json")
        self.assertEqual(result.code, 1, result)
        error = json.loads(result.out)["accounts"][0]["error"]
        self.assertIn("launcher is stale", error)
        self.assertIn("multi-codex apply", error)
        self.assertIn("update launcher", self.ok("apply").out)
        self.assertNotIn("stale", self.ok("list").out)
        with open(self.launcher_file("work")) as handle:
            self.assertIn("unset CODEX_ACCESS_TOKEN CODEX_API_KEY CODEX_SQLITE_HOME", handle.read())
        live = self.run_cli("usage", "--live", "--json", env={"FAKE_APP_SERVER_MODE": "ok"})
        self.assertEqual(live.code, 0, live)

    def test_warnings_mention_launchers(self):
        result = self.ok("list", env={"CODEX_API_KEY": "x"})
        self.assertIn("CODEX_API_KEY", result.err)
        self.assertIn("launchers clear it", result.err)
        report = json.loads(self.run_cli("doctor", "--json", env={"CODEX_API_KEY": "x"}).out)
        check = next(item for item in report["checks"] if item["id"] == "env")
        self.assertEqual(check["status"], "warn")
        self.assertIn("launchers override or clear them", check["message"])


class UnshareableTest(IsolationBase):
    """T7、T8：禁止共享清单。"""

    def test_is_unshareable(self):
        for item in ("auth.json", "Auth.JSON", "secrets", ".env", "installation_id", "sqlite", "State_5.SQLITE",
                     "logs_2.sqlite-wal", "queue_1.sqlite-shm", "sessions", "archived_sessions",
                     "session_index.jsonl", "app-server-control", "app-server-daemon", "packages", "tmp", ".tmp",
                     "log", "shell_snapshots"):
            self.assertTrue(config_module.is_unshareable(item), item)
        for item in ("AGENTS.md", "skills", "rules", "agents", "config.toml", "history.jsonl", "models_cache.json",
                     "cache", "sqlite-notes.md"):
            self.assertFalse(config_module.is_unshareable(item), item)

    def test_config_with_forbidden_item(self):
        self.ok("add", "work")
        for item in ("auth.json", "State_5.SQLITE", "sessions"):
            with self.subTest(item=item):
                data = self.config()
                data["shared"]["items"] = ["skills", item]
                self.write_config(data)
                result = self.run_cli("list")
                self.assertEqual(result.code, 1, result)
                self.assertIn(repr(item), result.err)
                self.assertIn("edit ", result.err)
                report = json.loads(self.run_cli("doctor", "--json").out)
                check = next(entry for entry in report["checks"] if entry["id"] == "config")
                self.assertEqual(check["status"], "fail")

    def test_init_shared_items(self):
        self.ok("init", "--shared-items", "skills,history.jsonl")
        self.assertEqual(self.config()["shared"]["items"], ["skills", "history.jsonl"])
        before = self.snapshot()
        result = self.run_cli("init", "--shared-items", "skills,logs_2.sqlite-wal")
        self.assertEqual(result.code, 2, result)
        self.assertIn("logs_2.sqlite-wal", result.err)
        self.assertEqual(self.snapshot(), before)


class SharedExcludeTest(IsolationBase):
    """T9–T16：按账号退出共享项。"""

    def setUp(self):
        super().setUp()
        self.setup_shared()
        self.ok("add", "a", "--shared")

    def managed(self, name):
        return self.config()["accounts"][name]["managed_links"]

    def test_exclude_and_include(self):
        link = os.path.join(self.account_dir("a"), "skills")
        self.assertTrue(os.path.islink(link))
        result = self.ok("set", "a", "--shared-exclude", "skills")
        self.assertIn("sharing disabled for this item", result.out)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.islink(os.path.join(self.account_dir("a"), "rules")))
        self.assertEqual(self.managed("a"), ["AGENTS.md", "rules"])
        self.assertEqual(self.config()["accounts"]["a"]["shared_exclude"], ["skills"])
        self.assertTrue(os.path.exists(os.path.join(self.shared, "skills", "keep.md")))
        self.assertIn("already up to date", self.ok("apply").out)

        self.ok("set", "a", "--shared-include", "skills")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(self.managed("a"), ["AGENTS.md", "skills", "rules"])
        self.assertNotIn("shared_exclude", self.config()["accounts"]["a"])

    def test_user_replaced_item_is_left_alone(self):
        # 先建 b：之后任何写命令都会收敛全部账号，a 的受管位置被换成真实目录时会判冲突。
        self.ok("add", "b")
        link = os.path.join(self.account_dir("a"), "skills")
        os.unlink(link)
        os.makedirs(link)
        own = os.path.join(self.account_dir("b"), "skills")
        os.makedirs(own)
        result = self.ok("set", "a", "--shared-exclude", "skills")
        self.assertIn("no longer the link multi-codex created", result.out)
        self.assertTrue(os.path.isdir(link))
        result = self.ok("set", "b", "--shared-exclude", "skills")
        self.assertNotIn("skills", result.out)
        self.assertTrue(os.path.isdir(own))

    def test_exclude_before_sharing(self):
        self.ok("add", "b")
        result = self.ok("set", "b", "--shared-exclude", "skills")
        self.assertIn("sharing is off for b", result.out)
        self.ok("set", "b", "--shared")
        self.assertFalse(os.path.lexists(os.path.join(self.account_dir("b"), "skills")))
        self.assertTrue(os.path.islink(os.path.join(self.account_dir("b"), "rules")))

    def test_errors_and_notes(self):
        before = self.snapshot()
        for args in (("--shared-exclude", "skills", "--shared-include", "skills"), ("--shared-exclude", "a/b")):
            result = self.run_cli("set", "a", *args)
            self.assertEqual(result.code, 2, result)
        self.assertEqual(self.snapshot(), before)
        result = self.ok("set", "a", "--shared-exclude", "agents")
        self.assertIn("agents is not in shared.items", result.out)
        # 大小写敏感：Skills 不是 skills
        result = self.ok("set", "a", "--shared-exclude", "Skills")
        self.assertIn("Skills is not in shared.items", result.out)
        self.assertTrue(os.path.islink(os.path.join(self.account_dir("a"), "skills")))

    def test_config_copy_when_config_toml_excluded(self):
        self.ok("init", "--shared-items", "AGENTS.md,skills,rules,config.toml")
        self.write(os.path.join(self.shared, "config.toml"), "model = 'x'\n")
        self.ok("add", "b")
        self.write(os.path.join(self.account_dir("b"), "config.toml"), "model = 'b'\n")
        self.ok("add", "c", "--shared")
        result = self.run_cli("set", "c", "--config-from", "b")
        self.assertEqual(result.code, 3, result)
        # 同一条命令里同时排除并复制会判冲突：计划按当前文件判断，那时 config.toml 还是指向共享内容的链接。
        self.assertEqual(self.run_cli("set", "c", "--shared-exclude", "config.toml", "--config-from", "b").code, 3)
        self.ok("set", "c", "--shared-exclude", "config.toml")
        self.ok("set", "c", "--config-from", "b")
        with open(os.path.join(self.account_dir("c"), "config.toml")) as handle:
            self.assertEqual(handle.read(), "model = 'b'\n")

    def test_list_output(self):
        self.ok("set", "a", "--shared-exclude", "skills", "--shared-exclude", "rules")
        self.assertIn("yes (not: skills, rules)", self.ok("list").out)
        self.assertIn("yes (not: skills, rules)", self.ok("list", "-v").out)
        data = json.loads(self.ok("list", "--json").out)
        self.assertEqual(data["accounts"][0]["shared_exclude"], ["skills", "rules"])

    def test_apply_file(self):
        self.ok("set", "a", "--shared-exclude", "skills")
        data = self.config()
        path = os.path.join(self.tmp, "accounts.json")
        del data["accounts"]["a"]["shared_exclude"]
        self.write(path, json.dumps(data))
        self.ok("apply", "-f", path)
        self.assertTrue(os.path.islink(os.path.join(self.account_dir("a"), "skills")))
        data["accounts"]["a"]["shared_exclude"] = ["rules"]
        self.write(path, json.dumps(data))
        self.ok("apply", "-f", path)
        self.assertFalse(os.path.lexists(os.path.join(self.account_dir("a"), "rules")))

    def test_dry_run(self):
        before = self.snapshot()
        result = self.ok("set", "a", "--shared-exclude", "skills", "--dry-run")
        self.assertIn("sharing disabled for this item", result.out)
        self.assertEqual(self.snapshot(), before)


class RenameTest(IsolationBase):
    """T17–T23：rename。"""

    def setUp(self):
        super().setUp()
        self.setup_shared()
        self.ok("add", "work", "--shared", "--proxy", "7901")
        self.write(os.path.join(self.account_dir("work"), "auth.json"), "{}", 0o600)
        self.ok("add", "other")

    def test_rename(self):
        before = self.snapshot(self.account_dir("work"))
        result = self.ok("rename", "work", "job")
        self.assertIn("renamed work to job; directory ~/.cx/work is unchanged", result.out)
        self.assertIn("delete launcher", result.out)
        self.assertFalse(os.path.lexists(self.launcher_file("work")))
        self.assertTrue(os.path.exists(self.launcher_file("job")))
        self.assertEqual(self.snapshot(self.account_dir("work")), before)
        account = self.config()["accounts"]["job"]
        self.assertEqual(account["dir"], "work")
        self.assertEqual(account["managed_links"], ["AGENTS.md", "skills", "rules"])
        self.assertEqual(account["proxy"], "http://127.0.0.1:7901")
        self.assertEqual(list(self.config()["accounts"]), ["job", "other"])
        _, _, env, _ = self.run_launcher("job")
        self.assertEqual(env["CODEX_HOME"], self.account_dir("work"))
        self.assertIn("already up to date", self.ok("apply").out)
        report = json.loads(self.run_cli("doctor", "--json").out)
        drift = next(item for item in report["checks"] if item["id"] == "drift")
        self.assertEqual(drift["status"], "ok", drift)
        self.assertEqual(self.ok("path", "job").out.strip(), self.account_dir("work"))

    def test_default_and_bindings(self):
        self.ok("use", "work")
        project = os.path.join(self.tmp, "project")
        os.makedirs(project)
        self.ok("bind", "work", project)
        self.ok("rename", "work", "job")
        self.assertEqual(self.ok("use").out.strip(), "job")
        self.assertIn("job", self.ok("bind").out)
        result = self.run_cli("run", "--", "codex", "x", cwd=project)
        self.assertEqual(result.code, 0, result)
        self.assertIn("using account job", result.err)
        self.assertEqual(self.recorded_env()["CODEX_HOME"], self.account_dir("work"))

    def test_rename_back(self):
        original = self.config()
        self.ok("rename", "work", "job")
        self.ok("rename", "job", "work")
        self.assertEqual(self.config(), original)

    def test_failures(self):
        before = self.snapshot()
        self.assertEqual(self.run_cli("rename", "ghost", "x").code, 1)
        self.assertEqual(self.run_cli("rename", "work", "Work").code, 2)
        self.assertEqual(self.run_cli("rename", "work", "bad/name").code, 2)
        result = self.run_cli("rename", "work", "other")
        self.assertEqual(result.code, 3, result)
        self.assertIn("already exists", result.err)
        self.assertEqual(self.snapshot(), before)
        self.ok("rename", "work", "job")
        before = self.snapshot()
        for args in (("add", "work"), ("rename", "other", "work"), ("rename", "other", "WORK")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.code, 3, result)
                self.assertIn("would use the directory of account", result.err)
                self.assertEqual(self.snapshot(), before)

    def test_migrate_default_into_renamed_dir(self):
        self.ok("rename", "work", "job")
        source = os.path.join(self.home, ".codex")
        os.makedirs(source)
        before = self.snapshot()
        result = self.run_cli("migrate-default", "work", "--skip-process-check")
        self.assertEqual(result.code, 3, result)
        self.assertEqual(self.snapshot(), before)
        os.rmdir(source)
        os.symlink(self.account_dir("work"), source)
        before = self.snapshot()
        result = self.run_cli("migrate-default", "work", "--skip-process-check")
        self.assertEqual(result.code, 3, result)
        self.assertEqual(self.snapshot(), before)

    def test_restore_renamed_account(self):
        self.ok("use", "work")
        self.ok("rename", "work", "job")
        result = self.ok("restore", "job")
        self.assertIn("restored job to ~/.codex", result.out)
        link = os.path.join(self.home, ".codex")
        self.assertFalse(os.path.islink(link))
        self.assertTrue(os.path.exists(os.path.join(link, "auth.json")))
        self.assertFalse(os.path.lexists(self.account_dir("work")))
        self.assertFalse(os.path.lexists(self.launcher_file("job")))

    def test_restore_resume_after_config_written(self):
        self.ok("use", "work")
        self.ok("rename", "work", "job")
        crashed = self.run_cli("restore", "job", env={"MULTI_CODEX_TEST_CRASH_AT": "restore-renamed"})
        self.assertEqual(crashed.code, CRASH, crashed)
        # 模拟“注销已写进配置、启动命令还没删”：配置里没有 job，标记仍在。
        data = self.config()
        del data["accounts"]["job"]
        self.write_config(data)
        result = self.ok("restore", "job")
        self.assertIn("from state C", result.out)
        self.assertFalse(os.path.exists(os.path.join(self.state, "restore-journal.json")))
        self.assertFalse(os.path.lexists(self.launcher_file("job")))

    def test_hand_written_config(self):
        cases = (
            ({"a": {"dir": "x"}, "b": {"dir": "X"}}, "would use the directory"),
            ({"a": {}, "b": {"dir": "a"}}, "would use the directory"),
            ({"a": {"dir": "../x"}}, "'dir' must be"),
        )
        for accounts, message in cases:
            with self.subTest(accounts=accounts):
                self.write_config({"version": 1, "accounts": accounts})
                result = self.run_cli("list")
                self.assertEqual(result.code, 1, result)
                self.assertIn(message, result.err)

    def test_apply_file_keeps_dir(self):
        self.ok("rename", "work", "job")
        data = self.config()
        path = os.path.join(self.tmp, "accounts.json")
        del data["accounts"]["job"]["dir"]
        self.write(path, json.dumps(data))
        result = self.ok("apply", "-f", path)
        self.assertNotIn("is ignored", result.err)
        self.assertEqual(self.config()["accounts"]["job"]["dir"], "work")
        data["accounts"]["job"]["dir"] = "elsewhere"
        self.write(path, json.dumps(data))
        result = self.ok("apply", "-f", path)
        self.assertIn("'dir' of 'job'", result.err)
        self.assertEqual(self.config()["accounts"]["job"]["dir"], "work")

    def test_app_data_dir_follows_directory(self):
        # 函数级：cmd_code / cmd_app 传 account.dir_name，改名后仍是原来的 .apps/work。
        self.ok("rename", "work", "job")
        data = self.config()
        cfg = config_module.parse_config(json.dumps(data), "config.json")
        cfg.root = self.root
        path = apps.gui_data_dir(cfg, cfg.find("job").dir_name, "vscode")
        self.assertEqual(path, os.path.join(self.root, ".apps", "work", "vscode"))

    def test_dry_run(self):
        before = self.snapshot()
        result = self.ok("rename", "work", "job", "--dry-run")
        self.assertIn("codex-job", result.out)
        self.assertNotIn("renamed work", result.out)
        self.assertEqual(self.snapshot(), before)


class HelpAndCompletionTest(unittest.TestCase):
    """T24。"""

    def test_help_and_completion(self):
        advanced = dict(cli.COMMAND_GROUPS)["Advanced"]
        self.assertIn("rename", [name for name, _ in advanced])
        spec = completion.command_spec(cli.build_parser())
        self.assertIn("rename", spec)
        for command in ("add", "set"):
            options, valued = spec[command]
            for option in ("--shared-exclude", "--shared-include"):
                self.assertIn(option, options)
                self.assertIn(option, valued)
        self.assertIn("rename", completion.ACCOUNT_COMMANDS)


if __name__ == "__main__":
    unittest.main()
