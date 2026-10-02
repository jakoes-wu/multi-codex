"""feature-account-insight §8：list 的身份列、usage、doctor 与 --json。

所有用例都在临时 HOME 中运行；令牌是现场构造的假 JWT，断言里核对它们不出现在任何输出中。
"""

import base64
import json
import os
import subprocess
import time
import unittest

from helpers import CliTestCase

SECRET = "SECRET-TOKEN-VALUE"


def make_jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "eyJhbGciOiJub25lIn0." + payload + "." + SECRET


def chatgpt_claims(email="a@example.com", plan="plus", user="user-1", workspace="ws-1"):
    claims = {"https://api.openai.com/auth": {"chatgpt_plan_type": plan, "chatgpt_user_id": user,
                                              "chatgpt_account_id": workspace}}
    if email:
        claims["email"] = email
    return claims


def rate_limits(used=12.0, minutes=300, resets_in=3600, limit_id="codex", secondary=None):
    return {"limit_id": limit_id, "limit_name": None,
            "primary": {"used_percent": used, "window_minutes": minutes, "resets_at": int(time.time()) + resets_in},
            "secondary": secondary, "credits": {"has_credits": False, "unlimited": False, "balance": None},
            "plan_type": "plus"}


def token_count_line(limits, timestamp="2026-09-30T01:13:14.123Z"):
    record = {"timestamp": timestamp, "type": "event_msg",
              "payload": {"type": "token_count", "info": None, "rate_limits": limits}}
    # Codex 用 serde 输出紧凑 JSON，usage 按 `"rate_limits":{` 片段预过滤，所以这里也必须紧凑。
    return json.dumps(record, separators=(",", ":"))


class InsightBase(CliTestCase):
    def account_dir(self, name):
        return os.path.join(self.root, name)

    def add(self, name):
        self.ok("add", name)
        return self.account_dir(name)

    def write_auth(self, name, claims=None, data=None):
        if data is None:
            data = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                    "tokens": {"id_token": make_jwt(claims), "access_token": SECRET, "refresh_token": SECRET,
                               "account_id": "ws-1"}, "last_refresh": "2026-09-30T00:00:00Z"}
        self.write(os.path.join(self.account_dir(name), "auth.json"),
                   data if isinstance(data, str) else json.dumps(data), 0o600)

    def write_rollout(self, name, lines, day="2026/09/30", file_name="rollout-a.jsonl", mtime=None,
                      archived=False):
        if archived:
            path = os.path.join(self.account_dir(name), "archived_sessions", file_name)
        else:
            path = os.path.join(self.account_dir(name), "sessions", day, file_name)
        self.write(path, "\n".join(lines) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def list_json(self, *extra, env=None):
        result = self.ok("list", "--json", *extra, env=env)
        return json.loads(result.out), result

    def assert_no_secret(self, result):
        self.assertNotIn(SECRET, result.out)
        self.assertNotIn(SECRET, result.err)


class IdentityTest(InsightBase):
    def test_list_shows_email_and_plan_without_tokens(self):
        self.add("work")
        self.write_auth("work", chatgpt_claims("w@example.com", "plus"))
        result = self.ok("list", "--verbose")
        header, row = [line for line in result.out.splitlines() if line.startswith(("NAME", "work"))]
        self.assertEqual(header.split(), ["NAME", "DIR", "PROXY", "SHARED", "LAUNCHER", "LOGIN", "PLAN"])
        self.assertEqual(row.split()[5:], ["w@example.com", "plus"])
        self.assert_no_secret(result)
        data, result = self.list_json()
        self.assertEqual(data["accounts"][0]["login"], {"type": "chatgpt", "email": "w@example.com", "plan": "plus"})
        self.assert_no_secret(result)

    def test_profile_email_fallback(self):
        self.add("work")
        claims = chatgpt_claims(email=None)
        claims["https://api.openai.com/profile"] = {"email": "p@example.com"}
        self.write_auth("work", claims)
        data, _ = self.list_json()
        self.assertEqual(data["accounts"][0]["login"]["email"], "p@example.com")

    def test_other_login_states(self):
        for name in ("key", "none", "ring", "bad"):
            self.add(name)
        self.write_auth("key", data={"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"})
        self.write(os.path.join(self.account_dir("ring"), "config.toml"), 'cli_auth_credentials_store = "keyring"\n')
        self.write_auth("bad", data="{not json")
        data, _ = self.list_json()
        types = {account["name"]: account["login"]["type"] for account in data["accounts"]}
        self.assertEqual(types, {"key": "apikey", "none": "logged-out", "ring": "keyring", "bad": "unreadable"})
        result = self.ok("list", "--verbose")
        rows = {line.split()[0]: line.split()[5:] for line in result.out.splitlines()[4:]}
        self.assertEqual(rows["key"], ["api-key", "-"])
        self.assertEqual(rows["none"], ["-", "-"])
        self.assertEqual(rows["ring"], ["keyring", "-"])
        self.assertEqual(rows["bad"], ["unreadable", "-"])

    def test_duplicates(self):
        for name in ("a", "b", "c", "d", "e"):
            self.add(name)
        self.write_auth("a", chatgpt_claims("x@example.com", user="u1", workspace="w1"))
        self.write_auth("b", chatgpt_claims("x@example.com", user="u1", workspace="w1"))
        # 同一个用户在另一个工作区：额度独立，不算重复。
        self.write_auth("c", chatgpt_claims("x@example.com", user="u1", workspace="w2"))
        # 两个都缺 ID：不参与比较。
        self.write_auth("d", {"email": "y@example.com"})
        self.write_auth("e", {"email": "y@example.com"})
        data, result = self.list_json()
        self.assertEqual(data["duplicates"], [["a", "b"]])
        self.assertIn("accounts a, b are logged in as the same ChatGPT account", result.err)
        self.assertEqual(result.err.count("same ChatGPT account"), 1)

    def test_first_five_columns_unchanged(self):
        self.add("work")
        result = self.ok("list", "--verbose")
        lines = result.out.splitlines()
        # 表头之前的说明行在 v0.4 多了一行 default:，按内容定位表头。
        header = next(index for index, line in enumerate(lines) if line.startswith("NAME"))
        self.assertEqual(lines[header].split()[:5], ["NAME", "DIR", "PROXY", "SHARED", "LAUNCHER"])
        self.assertEqual(lines[header + 1].split()[:5], ["work", "ok", "inherit", "no", "ok"])

    def test_system_config_keyring(self):
        self.add("work")
        self.write_auth("work", chatgpt_claims())
        system = self.write(os.path.join(self.tmp, "system.toml"), 'cli_auth_credentials_store = "keyring"\n')
        data, _ = self.list_json(env={"MULTI_CODEX_TEST_SYSTEM_CONFIG": system})
        self.assertEqual(data["accounts"][0]["login"]["type"], "keyring")
        self.assertEqual(data["accounts"][0]["credentials_store"], "keyring")

    def test_keyring_ignores_leftover_auth_file(self):
        self.add("a")
        self.add("b")
        self.write_auth("a", chatgpt_claims(user="u1", workspace="w1"))
        self.write_auth("b", chatgpt_claims(user="u1", workspace="w1"))
        self.write(os.path.join(self.account_dir("b"), "config.toml"), 'cli_auth_credentials_store = "keyring"\n')
        data, result = self.list_json()
        self.assertEqual(data["accounts"][1]["login"]["type"], "keyring")
        self.assertEqual(data["duplicates"], [])


class LocalUsageTest(InsightBase):
    def usage_json(self, *names):
        result = self.run_cli("usage", "--json", *names)
        return json.loads(result.out), result

    def test_skips_null_and_prefers_newest_file(self):
        self.add("work")
        now = time.time()
        self.write_rollout("work", [token_count_line(rate_limits(used=99.0))], file_name="rollout-old.jsonl",
                           mtime=now - 1000)
        self.write_rollout("work", [token_count_line(rate_limits(used=30.0)), token_count_line(None)],
                           file_name="rollout-new.jsonl", mtime=now)
        data, result = self.usage_json()
        self.assertEqual(result.code, 0, result)
        account = data["accounts"][0]
        self.assertTrue(account["ok"])
        self.assertEqual(account["snapshot_time"], "2026-09-30T01:13:14Z")
        self.assertEqual(account["limits"][0]["windows"][0]["used_percent"], 30.0)

    def test_newest_snapshot_per_limit_id(self):
        self.add("work")
        self.write_rollout("work", [token_count_line(rate_limits(used=1.0)),
                                    token_count_line(rate_limits(used=2.0, limit_id="other")),
                                    token_count_line(rate_limits(used=3.0))])
        data, _ = self.usage_json()
        used = {limit["limit_id"]: limit["windows"][0]["used_percent"] for limit in data["accounts"][0]["limits"]}
        self.assertEqual(used, {"codex": 3.0, "other": 2.0})

    def test_archived_only_and_no_data(self):
        self.add("work")
        self.add("empty")
        self.write_rollout("work", [token_count_line(rate_limits(used=7.0))], archived=True)
        data, result = self.usage_json()
        self.assertEqual(result.code, 0, result)
        by_name = {account["name"]: account for account in data["accounts"]}
        self.assertEqual(by_name["work"]["limits"][0]["windows"][0]["used_percent"], 7.0)
        self.assertTrue(by_name["empty"]["ok"])
        self.assertEqual(by_name["empty"]["limits"], [])
        human = self.ok("usage", "empty")
        self.assertIn("no usage data in local session logs", human.out)

    def test_reset_since_snapshot_and_shared_sessions(self):
        self.add("work")
        shared_sessions = os.path.join(self.tmp, "shared-sessions")
        os.makedirs(shared_sessions)
        os.symlink(shared_sessions, os.path.join(self.account_dir("work"), "sessions"))
        self.write_rollout("work", [token_count_line(rate_limits(used=50.0, resets_in=-60))])
        result = self.ok("usage", "work")
        self.assertIn("reset since snapshot", result.out)
        self.assertNotIn("50% used", result.out)
        self.assertIn("sessions is a link", result.out)
        data, _ = self.usage_json("work")
        self.assertTrue(data["accounts"][0]["sessions_shared"])

    def test_snapshot_near_chunk_boundary(self):
        self.add("work")
        target = token_count_line(rate_limits(used=42.0))
        # 让目标行跨过 64 KiB 的块边界，后面再跟大量不含额度的行。
        filler_before = ['{"type":"response_item","payload":{"text":"' + "x" * 1000 + '"}}'] * 65
        filler_after = ['{"type":"response_item","payload":{"text":"' + "y" * 999 + '"}}'] * 70
        self.write_rollout("work", filler_before + [target] + filler_after)
        data, _ = self.usage_json()
        self.assertEqual(data["accounts"][0]["limits"][0]["windows"][0]["used_percent"], 42.0)

    def test_unknown_name_fails_others_still_shown(self):
        self.add("work")
        self.write_rollout("work", [token_count_line(rate_limits(used=5.0))])
        data, result = self.usage_json("work", "ghost")
        self.assertEqual(result.code, 1, result)
        self.assertTrue(data["accounts"][0]["ok"])
        self.assertFalse(data["accounts"][1]["ok"])
        self.assertIn("not registered", data["accounts"][1]["error"])

    def test_no_accounts(self):
        result = self.ok("usage")
        self.assertIn("no accounts registered", result.out)
        result = self.ok("usage", "--json")
        self.assertEqual(json.loads(result.out), {"version": 1, "accounts": []})


class LiveUsageTest(InsightBase):
    def setUp(self):
        super().setUp()
        self.add("work")
        self.log = os.path.join(self.tmp, "server.log")

    def live(self, mode, *extra):
        result = self.run_cli("usage", "--live", "--json", *extra,
                              env={"FAKE_APP_SERVER_MODE": mode, "FAKE_APP_SERVER_LOG": self.log})
        data = json.loads(result.out) if result.out.strip() else None
        return data, result

    def test_ok_current_format(self):
        data, result = self.live("ok")
        self.assertEqual(result.code, 0, result)
        account = data["accounts"][0]
        self.assertEqual(account["source"], "live")
        self.assertEqual([limit["limit_id"] for limit in account["limits"]], ["codex", "codex_other"])
        windows = account["limits"][0]["windows"]
        self.assertEqual([(w["kind"], w["used_percent"], w["window_minutes"]) for w in windows],
                         [("primary", 12.0, 300), ("secondary", 40.0, 10080)])
        with open(self.log) as handle:
            requests = [json.loads(line) for line in handle]
        self.assertEqual([request.get("method") for request in requests],
                         ["initialize", "initialized", "account/rateLimits/read"])
        self.assertTrue(all("jsonrpc" not in request for request in requests))
        human = self.run_cli("usage", "--live", env={"FAKE_APP_SERVER_MODE": "ok"})
        self.assertIn("(plus, live)", human.out)
        self.assertIn("12% used", human.out)

    def test_error_modes(self):
        expectations = {
            "auth-error": "codex account authentication required to read rate limits",
            "api-key-error": "chatgpt authentication required to read rate limits",
            "no-method": "Codex 0.48.0 or newer is required",
            "garbage": "invalid response from app-server",
            "exit": "boom: fake app-server failed to start",
        }
        for mode, text in expectations.items():
            with self.subTest(mode=mode):
                data, result = self.live(mode)
                self.assertEqual(result.code, 1, result)
                self.assertFalse(data["accounts"][0]["ok"])
                self.assertIn(text, data["accounts"][0]["error"])

    def test_noise_is_skipped(self):
        data, result = self.live("noise")
        self.assertEqual(result.code, 0, result)
        self.assertEqual(data["accounts"][0]["limits"][0]["limit_id"], "codex")

    def test_legacy_format(self):
        data, result = self.live("legacy")
        self.assertEqual(result.code, 0, result)
        limit = data["accounts"][0]["limits"][0]
        self.assertEqual(limit["limit_id"], "codex")
        self.assertEqual(limit["windows"][0]["used_percent"], 12.5)
        self.assertEqual(limit["windows"][0]["window_minutes"], 300)

    def test_timeout_kills_whole_process_group(self):
        pids_file = os.path.join(self.tmp, "pids")
        started = time.monotonic()
        result = self.run_cli("usage", "--live", "--json", "--timeout", "2",
                              env={"FAKE_APP_SERVER_MODE": "hang", "FAKE_APP_SERVER_PIDS": pids_file})
        elapsed = time.monotonic() - started
        self.assertEqual(result.code, 1, result)
        self.assertIn("within 2s", json.loads(result.out)["accounts"][0]["error"])
        self.assertGreaterEqual(elapsed, 2)
        self.assertLess(elapsed, 2 + 2 + 3)  # 超时 + SIGTERM 等待 + 解释器启动的余量
        with open(pids_file) as handle:
            pids = [int(pid) for pid in handle.read().split()]
        for pid in pids:
            alive = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE,
                                   universal_newlines=True).stdout.strip()
            self.assertTrue(alive == "" or alive.startswith("Z"), "pid {} still running: {}".format(pid, alive))

    def test_stale_launcher_not_run(self):
        launcher = os.path.join(self.bin, "codex-work")
        with open(launcher, "a") as handle:
            handle.write("# edited\n")
        data, result = self.live("ok")
        self.assertEqual(result.code, 1, result)
        self.assertIn("multi-codex apply", data["accounts"][0]["error"])
        self.assertFalse(os.path.exists(self.log))

    def test_invalid_timeout(self):
        for value in ("0", "-1", "abc"):
            with self.subTest(value=value):
                self.assertEqual(self.run_cli("usage", "--live", "--timeout", value).code, 2)


class DoctorTest(InsightBase):
    def doctor(self, env=None):
        result = self.run_cli("doctor", "--json", env=env)
        data = json.loads(result.out)
        return {check["id"]: check for check in data["checks"]}, data, result

    def healthy_env(self):
        return {"PATH": os.pathsep.join([self.bin, self.env["PATH"]])}

    def test_all_ok(self):
        self.add("work")
        self.write_auth("work", chatgpt_claims())
        checks, data, result = self.doctor(self.healthy_env())
        self.assertEqual(result.code, 0, result)
        self.assertTrue(data["ok"])
        self.assertEqual({check["status"] for check in checks.values()}, {"ok"}, checks)
        human = self.run_cli("doctor", env=self.healthy_env())
        self.assertTrue(human.out.rstrip().endswith("0 warn, 0 fail"), human)

    def test_codex_missing(self):
        os.unlink(os.path.join(self.fakebin, "codex"))
        checks, _, result = self.doctor()
        self.assertEqual(result.code, 1, result)
        self.assertEqual(checks["codex"]["status"], "fail")

    def test_old_codex_warns(self):
        checks, _, _ = self.doctor({"FAKE_CODEX_VERSION": "0.47.0"})
        self.assertEqual(checks["codex"]["status"], "warn")

    def test_path_and_env_warnings(self):
        self.add("work")
        self.write_auth("work", chatgpt_claims())
        checks, _, result = self.doctor({"CODEX_HOME": "/elsewhere"})
        self.assertEqual(result.code, 0, result)
        self.assertEqual(checks["path"]["status"], "warn")
        self.assertEqual(checks["env"]["status"], "warn")
        self.assertIn("CODEX_HOME", checks["env"]["message"])

    def test_drift_then_apply(self):
        shared = os.path.join(self.tmp, "shared")
        self.write(os.path.join(shared, "AGENTS.md"), "rules")
        self.ok("init", "--shared-dir", shared, "--shared-items", "AGENTS.md")
        self.add("work")
        self.ok("add", "work", "--shared")
        self.write_auth("work", chatgpt_claims())
        os.unlink(os.path.join(self.bin, "codex-work"))
        os.unlink(os.path.join(self.account_dir("work"), "AGENTS.md"))
        config_path = os.path.join(self.state, "config.json")
        with open(config_path, "rb") as handle:
            before = handle.read()
        checks, _, result = self.doctor(self.healthy_env())
        self.assertEqual(result.code, 0, result)
        self.assertEqual(checks["drift"]["status"], "warn")
        self.assertEqual(checks["drift"]["hint"], "multi-codex apply")
        self.assertEqual(len(checks["drift"]["details"]), 2)
        with open(config_path, "rb") as handle:
            self.assertEqual(before, handle.read())
        self.ok("apply")
        checks, _, _ = self.doctor(self.healthy_env())
        self.assertEqual(checks["drift"]["status"], "ok")

    def test_drift_conflict(self):
        shared = os.path.join(self.tmp, "shared")
        self.write(os.path.join(shared, "skills", "x"), "x")
        self.ok("init", "--shared-dir", shared, "--shared-items", "skills")
        self.ok("add", "work", "--shared")
        os.unlink(os.path.join(self.account_dir("work"), "skills"))
        os.makedirs(os.path.join(self.account_dir("work"), "skills"))
        checks, _, result = self.doctor(self.healthy_env())
        self.assertEqual(result.code, 1, result)
        self.assertEqual(checks["drift"]["status"], "fail")

    def test_skip_is_only_a_note(self):
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(shared)
        self.ok("init", "--shared-dir", shared, "--shared-items", "AGENTS.md")
        self.ok("add", "work", "--shared")
        self.write_auth("work", chatgpt_claims())
        checks, _, _ = self.doctor(self.healthy_env())
        self.assertEqual(checks["drift"]["status"], "ok")
        self.assertTrue(any(detail.startswith("skip ") for detail in checks["drift"]["details"]))
        self.ok("apply")
        checks, _, _ = self.doctor(self.healthy_env())
        self.assertEqual(checks["drift"]["status"], "ok")

    def test_pending_and_corrupt_journal(self):
        self.add("work")
        os.makedirs(self.state, exist_ok=True)
        journal = os.path.join(self.state, "migrate-journal.json")
        self.write(journal, json.dumps({"name": "main", "source": "/s", "target": "/t", "backup": "/b",
                                        "mode": "rename", "phase": "moved"}))
        checks, _, result = self.doctor()
        self.assertEqual(result.code, 1, result)
        self.assertEqual(checks["migration"]["status"], "fail")
        self.write(journal, "{broken")
        checks, data, result = self.doctor()
        self.assertEqual(result.code, 1, result)
        self.assertEqual(checks["migration"]["status"], "fail")
        self.assertIn("account:work", checks)

    def test_account_states(self):
        self.add("none")
        self.add("gone")
        os.rmdir(self.account_dir("gone"))
        checks, _, result = self.doctor(self.healthy_env())
        self.assertEqual(checks["account:none"]["status"], "warn")
        self.assertEqual(checks["account:none"]["hint"], "multi-codex login none")
        self.assertEqual(checks["account:gone"]["status"], "fail")
        self.assertEqual(result.code, 1, result)

    def test_default_dir_states(self):
        # fix-doctor-default-dir：~ 必须展开；修复前第 1-3 种状态都会被误报为“不存在”。
        self.add("work")
        link = os.path.join(self.home, ".codex")
        os.symlink(self.account_dir("work"), link)
        checks, _, _ = self.doctor()
        self.assertEqual(checks["default-dir"]["status"], "ok")
        self.assertIn("links to account work", checks["default-dir"]["message"])
        os.unlink(link)
        os.symlink(self.tmp, link)
        checks, _, _ = self.doctor()
        self.assertEqual(checks["default-dir"]["status"], "warn")
        os.unlink(link)
        os.makedirs(link)
        checks, _, _ = self.doctor()
        self.assertIn("not migrated", checks["default-dir"]["message"])
        os.rmdir(link)
        checks, _, _ = self.doctor()
        self.assertIn("does not exist", checks["default-dir"]["message"])

    def test_unconfigured(self):
        checks, _, result = self.doctor()
        self.assertEqual(result.code, 0, result)
        self.assertEqual(checks["config"]["status"], "warn")


class JsonOutputTest(InsightBase):
    def test_stdout_is_one_json_object_and_warnings_go_to_stderr(self):
        result = self.ok("list", "--json", env={"CODEX_SQLITE_HOME": "/x"})
        self.assertEqual(json.loads(result.out), {"version": 1, "configured": False, "accounts": [],
                                                  "duplicates": []})
        self.assertIn("CODEX_SQLITE_HOME", result.err)
        self.add("work")
        for args in (("list", "--json"), ("usage", "--json"), ("doctor", "--json")):
            with self.subTest(args=args):
                result = self.run_cli(*args, env={"CODEX_SQLITE_HOME": "/x"})
                self.assertEqual(json.loads(result.out)["version"], 1)


if __name__ == "__main__":
    unittest.main()
