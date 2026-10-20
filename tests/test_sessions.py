"""feature-session-list §8：sessions / conversations 跨账号、跨目录列出会话。"""

import json
import os
import shlex
import sys
import unittest

from helpers import SRC, CliTestCase

sys.path.insert(0, SRC)
from multi_codex import cli, completion  # noqa: E402

UUID = "01a157c4-3f6d-7452-96cf-e9f4cb89d{:03x}"
DEFAULT_LAUNCHER = "env -u CODEX_HOME -u CODEX_SQLITE_HOME -u CODEX_API_KEY -u CODEX_ACCESS_TOKEN codex"


def sid(number):
    return UUID.format(number)


def line(record):
    return json.dumps(record, ensure_ascii=False) + "\n"


def meta(session_id, cwd, created="2026-10-11T08:05:12.000Z", source="cli"):
    return line({"timestamp": created, "type": "session_meta",
                 "payload": {"id": session_id, "timestamp": created, "cwd": cwd, "source": source,
                             "originator": "codex-tui"}})


def user(text, timestamp="2026-10-11T08:06:00.000Z"):
    record = {"type": "response_item",
              "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}
    if timestamp:
        record["timestamp"] = timestamp
    return line(record)


def stamp(timestamp):
    return line({"timestamp": timestamp, "type": "event_msg", "payload": {"type": "token_count"}})


class SessionsTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.env["TZ"] = "Asia/Shanghai"
        self.env["COLUMNS"] = "300"

    def acct(self, name):
        return os.path.join(self.root, name)

    def default(self):
        return os.path.join(self.home, ".codex")

    def rollout(self, base, session_id, body="", cwd="/w/proj", created="2026-10-11T08:05:12.000Z",
                updated="2026-10-11T09:42:40.000Z", archived=False, first=None, day=("2026", "10", "11")):
        directory = os.path.join(base, "archived_sessions") if archived else os.path.join(base, "sessions", *day)
        name = "rollout-2026-10-11T16-05-12-{}.jsonl".format(session_id)
        content = (meta(session_id, cwd, created) if first is None else first) + body
        if updated:
            content += stamp(updated)
        return self.write(os.path.join(directory, name), content)

    def name(self, base, session_id, thread_name):
        path = os.path.join(base, "session_index.jsonl")
        with open(path, "a") as handle:
            handle.write(line({"id": session_id, "thread_name": thread_name, "updated_at": "2026-10-11T09:00:00Z"}))

    def sessions_json(self, *args):
        return json.loads(self.ok("sessions", "--json", *args).out)

    def only(self, *args):
        data = self.sessions_json(*args)
        self.assertEqual(len(data["sessions"]), 1, data)
        return data["sessions"][0]

    # ---- T1 文本输出 ----

    def test_text_output(self):
        self.ok("add", "work")
        self.ok("add", "home")
        self.rollout(self.acct("work"), sid(1), user("first question"), updated="2026-10-11T09:42:40.000Z")
        self.rollout(self.acct("work"), sid(2), user("second"), cwd="/w/other", updated="2026-10-12T01:00:00.000Z")
        self.rollout(self.acct("home"), sid(3), user("third"), updated="2026-10-10T00:00:00.000Z")
        lines = self.ok("sessions").out.splitlines()
        self.assertEqual(lines[0].split("  ")[0].strip(), "TITLE")
        self.assertRegex(lines[0], r"^TITLE +ACCOUNT +CREATED +UPDATED +SESSION ID$")
        self.assertEqual(len(lines), 4)
        self.assertIn("second", lines[1])
        self.assertIn("2026-10-12 09:00", lines[1])
        self.assertIn(sid(2), lines[1])
        self.assertIn("first question", lines[2])
        self.assertIn("2026-10-11 16:05  2026-10-11 17:42", lines[2])
        self.assertIn("home", lines[3])
        self.assertNotIn("resume", self.ok("sessions").out)

    # ---- T2 / T3 标题来源 ----

    def test_title_sources(self):
        self.ok("add", "work")
        base = self.acct("work")
        self.rollout(base, sid(1), user("a prompt"))
        self.name(base, sid(1), "old name")
        self.name(base, sid(1), "new name")
        self.rollout(base, sid(2), user("only a prompt"))
        self.rollout(base, sid(3), "")
        self.name(base, sid(3), " \x1b ")  # 只有空白与控制字符的名称不算
        by_id = {item["session_id"]: item for item in self.sessions_json()["sessions"]}
        self.assertEqual((by_id[sid(1)]["title"], by_id[sid(1)]["title_source"]), ("new name", "name"))
        self.assertEqual((by_id[sid(2)]["title"], by_id[sid(2)]["title_source"]), ("only a prompt", "prompt"))
        self.assertEqual((by_id[sid(3)]["title"], by_id[sid(3)]["title_source"]), ("", "none"))
        row = next(text for text in self.ok("sessions").out.splitlines() if sid(3) in text)
        self.assertTrue(row.startswith("-  "))

    def test_first_prompt_skips_injected_text(self):
        self.ok("add", "work")
        body = (user("<environment_context>\n<cwd>/w</cwd>\n</environment_context>")
                + user("# AGENTS.md instructions for /w\n\nrules")
                + line({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "   "}, {"type": "input_image", "image_url": "x"},
                    {"type": "input_text", "text": "the real question"}]}}))
        self.rollout(self.acct("work"), sid(1), body)
        self.assertEqual(self.only()["title"], "the real question")

    def test_legacy_user_message(self):
        self.ok("add", "work")
        body = line({"type": "event_msg", "payload": {"type": "user_message", "message": "legacy question"}})
        self.rollout(self.acct("work"), sid(1), body)
        self.assertEqual(self.only()["title"], "legacy question")

    # ---- T4 清洗 ----

    def test_title_cleaning(self):
        self.ok("add", "work")
        base = self.acct("work")
        self.rollout(base, sid(1), "")
        self.name(base, sid(1), "line1\nline2\t\x1b[31mred\u202e " + "x" * 300)
        self.rollout(base, sid(2), "")
        self.name(base, sid(2), "修复 解析 \U0001f468\u200d\U0001f4bb")
        out = self.ok("sessions").out
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\u202e", out)
        self.assertIn("line1 line2 [31mred " + "x" * 40 + "…", out)
        self.assertIn("修复 解析 \U0001f468\u200d\U0001f4bb", out)
        by_id = {item["session_id"]: item for item in self.sessions_json()["sessions"]}
        self.assertEqual(len(by_id[sid(1)]["title"]), 201)
        self.assertTrue(by_id[sid(1)]["title"].endswith("…"))

    # ---- T5 长行与头部额度 ----

    def test_long_lines(self):
        self.ok("add", "work")
        big = line({"type": "response_item", "payload": {"type": "function_call_output", "output": "y" * 200000}})
        self.rollout(self.acct("work"), sid(1), big + user("after a long line"))
        self.rollout(self.acct("work"), sid(2), big * 6 + user("too far"))
        by_id = {item["session_id"]: item for item in self.sessions_json()["sessions"]}
        self.assertEqual(by_id[sid(1)]["title"], "after a long line")
        self.assertEqual(by_id[sid(2)]["title_source"], "none")

    # ---- T6 子代理 ----

    def test_subagent_hidden(self):
        self.ok("add", "work")
        first = line({"type": "session_meta", "payload": {"id": sid(1), "cwd": "/w", "timestamp": "2026-10-11T08:05:12Z",
                                                          "source": {"subagent": "review"}}})
        self.rollout(self.acct("work"), sid(1), user("sub"), first=first)
        self.assertIn("no sessions found", self.ok("sessions").out)

    # ---- T7 兜底 ----

    def test_fallbacks(self):
        self.ok("add", "work")
        base = self.acct("work")
        path = self.rollout(base, sid(1), user("q", None) + '{"timestamp": "2026-1', first="not json\n", updated=None)
        os.utime(path, (1760000000, 1760000000))
        self.rollout(base, sid(2), user("q2"), cwd="relative/dir")
        self.rollout(base, sid(3), user("q3"), cwd="/w/a\nb")
        by_id = {item["session_id"]: item for item in self.sessions_json()["sessions"]}
        self.assertIsNone(by_id[sid(1)]["directory"])
        self.assertIsNone(by_id[sid(1)]["created"])
        self.assertEqual(by_id[sid(1)]["updated"], "2025-10-09T08:53:20Z")
        self.assertEqual(by_id[sid(1)]["resume"], "codex-work resume " + sid(1))
        self.assertIsNone(by_id[sid(2)]["directory"])
        self.assertIsNone(by_id[sid(3)]["directory"])
        out = self.ok("sessions", "--cmd").out
        self.assertIn("codex-work resume {}  (start directory unknown)".format(sid(1)), out)
        self.assertNotIn("start directory unknown", self.ok("sessions").out)
        narrow = self.ok("sessions", "--cmd", env={"COLUMNS": "40"}).out.splitlines()
        self.assertEqual(len(narrow), 1 + 2 * 3)

    def test_half_written_last_line(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q") + stamp("2026-10-11T10:00:00.000Z") + '{"timestamp": "2026-1',
                     updated=None)
        self.assertEqual(self.only()["updated"], "2026-10-11T10:00:00Z")

    def test_bad_config(self):
        self.write(os.path.join(self.state, "config.json"), "{not json")
        result = self.run_cli("sessions")
        self.assertEqual(result.code, 1, result)
        self.assertIn("is not valid JSON", result.err)
        self.assertNotIn("Traceback", result.err)
        # 名字不合法时仍是用法错误，不被配置损坏盖掉
        self.assertEqual(self.run_cli("sessions", "bad/name").code, 2)

    def test_bad_records_do_not_crash(self):
        self.ok("add", "work")
        body = "[" * 100000 + "\n" + line({"timestamp": "9999-12-31T23:59:59Z", "type": "x"})
        body += user("\x1b\x07") + user("after control-only prompt")
        path = self.rollout(self.acct("work"), sid(1), body, created="0001-01-01T00:00:00Z", updated=None)
        with open(path, "ab") as handle:
            handle.write(b"\xff\xfe not utf-8 \xc3\n")
        self.assertEqual(self.only()["title"], "after control-only prompt")
        for tz in ("America/Los_Angeles", "Asia/Tokyo"):
            with self.subTest(tz=tz):
                result = self.ok("sessions", env={"TZ": tz})
                self.assertNotIn("Traceback", result.err)
                self.ok("sessions", "--json", env={"TZ": tz})

    def test_lone_surrogate(self):
        # 只有半个表情的 \ud83d：名称、首条提问、目录里出现都不能让打印崩掉
        self.ok("add", "work")
        base = self.acct("work")
        half = "\\ud83d"  # 文件里是 JSON 转义的 6 个字符，不是真的代理字符（写不进 UTF-8 文件）
        self.rollout(base, sid(1), user("hi HALF there").replace("HALF", half), updated="2026-10-11T09:03:00.000Z")
        self.rollout(base, sid(2), user("q"), updated="2026-10-11T09:02:00.000Z")
        self.write(os.path.join(base, "session_index.jsonl"),
                   line({"id": sid(2), "thread_name": "named HALF"}).replace("HALF", half))
        self.rollout(base, sid(3), user("bad dir"), first=meta(sid(3), "/w/HALF").replace("HALF", half),
                     updated="2026-10-11T09:01:00.000Z")
        for args in ((), ("--cmd",)):
            result = self.ok("sessions", *args)
            self.assertNotIn("Traceback", result.err)
        data = self.sessions_json()["sessions"]
        self.assertEqual([entry["title"] for entry in data], ["hi there", "named", "bad dir"])
        self.assertIsNone(data[2]["directory"])

    def test_out_of_range_time(self):
        # 0001 年与 9999 年换成本地时间会越界，文本显示 -，JSON 照常给 UTC
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q", None), created="0001-01-01T00:00:00Z",
                     updated="9999-12-31T23:59:59Z")
        la = self.ok("sessions", env={"TZ": "America/Los_Angeles"}).out.splitlines()[1].split()
        self.assertEqual(la, ["q", "work", "-", "9999-12-31", "15:59", sid(1)])
        tokyo = self.ok("sessions", env={"TZ": "Asia/Tokyo"}).out.splitlines()[1].split()
        self.assertEqual(tokyo[-2:], ["-", sid(1)])
        entry = self.only()
        self.assertEqual((entry["created"], entry["updated"]), ("0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z"))

    # ---- T8 文件名 ----

    def test_ignored_files(self):
        self.ok("add", "work")
        base = self.acct("work")
        content = meta(sid(1), "/w") + user("q")
        self.write(os.path.join(base, "sessions", "2026", "10", "11", "rollout-x.jsonl"), content)
        self.write(os.path.join(base, "sessions", "2026", "10", "foo-{}.jsonl".format(sid(1))), content)
        self.write(os.path.join(base, "sessions", "2026", "10", "11", "deep",
                                "rollout-2026-10-11T16-05-12-{}.jsonl".format(sid(1))), content)
        self.write(os.path.join(base, "sessions", "2026", "10", "11",
                                "rollout-2026-10-11T16-05-12-{}.jsonl\n".format(sid(1))), content)
        self.write(os.path.join(base, "sessions", "2026", "10",
                                "rollout-2026-10-11T16-05-12-{}.jsonl".format(sid(1))), content)
        self.assertIn("no sessions found", self.ok("sessions").out)

    # ---- T9 归档 ----

    def test_archived(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("old work"), archived=True)
        self.assertTrue(self.only()["archived"])
        self.assertIn("[archived] old work", self.ok("sessions").out)
        self.assertIn("no sessions found", self.ok("sessions", "--grep", "archived").out)

    # ---- T10–T12 默认目录 ----

    def test_unregistered_default(self):
        self.rollout(self.default(), sid(1), user("in default"))
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(2), user("in work"))
        item = self.only("default")
        self.assertEqual((item["account"], item["registered"]), ("default", False))
        self.assertEqual(item["resume"], "cd /w/proj && {} resume {}".format(DEFAULT_LAUNCHER, sid(1)))
        self.assertEqual(self.only("DEFAULT")["session_id"], sid(1))

    def test_registered_default_wins(self):
        self.rollout(self.default(), sid(1), user("unregistered"))
        self.ok("add", "default")
        self.rollout(self.acct("default"), sid(2), user("registered"))
        items = self.sessions_json()["sessions"]
        self.assertEqual(sorted((item["registered"], item["resume"].split(" && ")[1].split(" resume")[0])
                                for item in items),
                         [(False, DEFAULT_LAUNCHER), (True, "codex-default")])
        self.assertEqual(self.only("default")["session_id"], sid(2))

    def test_default_link_listed_once(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q"))
        os.symlink(self.acct("work"), self.default())
        item = self.only()
        self.assertEqual(item["account"], "work")

    def test_no_config(self):
        self.assertIn("no sessions found", self.ok("sessions").out)
        self.rollout(self.default(), sid(1), user("q"))
        self.assertEqual(self.only()["account"], "default")

    # ---- T13 账号参数 ----

    def test_name_argument(self):
        self.ok("add", "work")
        self.ok("add", "home")
        self.rollout(self.acct("work"), sid(1), user("q"))
        self.rollout(self.acct("home"), sid(2), user("q"))
        self.assertEqual(self.only("work")["session_id"], sid(1))
        self.assertEqual(self.run_cli("sessions", "nosuch").code, 1)
        self.assertEqual(self.run_cli("sessions", "default").code, 1)
        self.assertEqual(self.run_cli("sessions", "bad name").code, 2)

    def test_bad_name_without_config(self):
        self.assertEqual(self.run_cli("sessions", "bad name").code, 2)

    # ---- T14 / T15 过滤与条数 ----

    def test_grep(self):
        self.ok("add", "work")
        base = self.acct("work")
        self.rollout(base, sid(1), user("Fix The Proxy"))
        self.rollout(base, sid(2), user("other"), cwd="/srv/special-dir")
        self.rollout(base, "aaaaaaaa-3f6d-7452-96cf-e9f4cb89d000", user("third"))
        self.assertEqual(self.only("--grep", "proxy")["session_id"], sid(1))
        self.assertEqual(self.only("--grep", "SPECIAL")["session_id"], sid(2))
        self.assertEqual(self.only("--grep", "aaaaaaaa")["title"], "third")

    def test_limit(self):
        self.ok("add", "work")
        for number in range(31):
            self.rollout(self.acct("work"), sid(number), user("q{}".format(number)),
                         updated="2026-10-11T09:{:02d}:00.000Z".format(number))
        out = self.ok("sessions", "--limit", "1").out.splitlines()
        self.assertEqual(len(out), 3)
        self.assertEqual(out[-1], "showing 1 of 31 sessions; use --limit 0 for all, or --grep TEXT to narrow")
        self.assertEqual(len(self.ok("sessions", "--limit", "0").out.splitlines()), 32)
        out = self.ok("sessions").out.splitlines()
        self.assertEqual(len(out), 32)
        self.assertTrue(out[-1].startswith("showing 30 of 31 sessions"))
        self.assertEqual(self.run_cli("sessions", "--limit", "-1").code, 2)
        data = self.sessions_json()
        self.assertEqual((len(data["sessions"]), data["total"]), (31, 31))

    # ---- T16 JSON ----

    def test_json(self):
        self.ok("add", "work")
        path = self.rollout(self.acct("work"), sid(1), user("hello"))
        data = self.sessions_json()
        self.assertEqual(data, {"version": 1, "total": 1, "skipped": 0, "sessions": [{
            "account": "work", "registered": True, "directory": "/w/proj", "session_id": sid(1),
            "title": "hello", "title_source": "prompt", "archived": False,
            "created": "2026-10-11T08:05:12Z", "updated": "2026-10-11T09:42:40Z", "file": path,
            "resume": "cd /w/proj && codex-work resume " + sid(1)}]})
        self.assertEqual(self.ok("sessions", "--json", "--cmd").out, self.ok("sessions", "--json").out)

    # ---- T17 读不了的文件 ----

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root 能读任何文件")
    def test_unreadable_file(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("ok"))
        os.chmod(self.rollout(self.acct("work"), sid(2), user("locked")), 0)
        result = self.ok("sessions")
        self.assertIn("skipped 1 session file(s) that could not be read", result.err)
        self.assertIn("ok", result.out)
        self.assertEqual(self.sessions_json()["skipped"], 1)

    # ---- T18 别名 ----

    def test_alias(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q"))
        self.assertEqual(self.ok("conversations").out, self.ok("sessions").out)
        self.assertEqual(self.ok("conversations", "work", "--json").out, self.ok("sessions", "work", "--json").out)
        self.assertEqual(self.run_cli("convrsations").code, 2)

    # ---- T19 / T20 --cmd 排版 ----

    def test_cmd_layouts(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q"))
        wide = self.ok("sessions", "--cmd").out.splitlines()
        self.assertRegex(wide[0], r"^TITLE +ACCOUNT +CREATED +UPDATED +CMD$")
        self.assertEqual(len(wide), 2)
        self.assertTrue(wide[1].endswith("cd /w/proj && codex-work resume " + sid(1)))
        narrow = self.ok("sessions", "--cmd", env={"COLUMNS": "40"}).out.splitlines()
        self.assertRegex(narrow[0], r"^TITLE +ACCOUNT +CREATED +UPDATED$")
        self.assertEqual(narrow[2], "  cd /w/proj && codex-work resume " + sid(1))
        self.assertNotIn(sid(1), narrow[1])

    def test_cmd_width_boundary(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q"))
        width = max(len(text) for text in self.ok("sessions", "--cmd").out.splitlines())
        self.assertEqual(len(self.ok("sessions", "--cmd", env={"COLUMNS": str(width)}).out.splitlines()), 2)
        self.assertEqual(len(self.ok("sessions", "--cmd", env={"COLUMNS": str(width - 1)}).out.splitlines()), 3)

    # ---- T21 中文对齐 ----

    def test_wide_char_alignment(self):
        self.ok("add", "work")
        base = self.acct("work")
        for number, title, updated in ((1, "修复解析", "2026-10-11T09:03:00.000Z"),
                                       (2, "abcdefgh", "2026-10-11T09:02:00.000Z")):
            self.rollout(base, sid(number), "", updated=updated)
            self.name(base, sid(number), title)
        lines = self.ok("sessions").out.splitlines()
        self.assertEqual(lines[1], "修复解析  work     2026-10-11 16:05  2026-10-11 17:03  " + sid(1))
        self.assertEqual(lines[2], "abcdefgh  work     2026-10-11 16:05  2026-10-11 17:02  " + sid(2))
        self.rollout(base, sid(3), "", updated="2026-10-11T09:01:00.000Z")
        self.name(base, sid(3), "修复解析器")
        lines = self.ok("sessions").out.splitlines()
        self.assertEqual(lines[2], "abcdefgh    work     2026-10-11 16:05  2026-10-11 17:02  " + sid(2))
        self.assertEqual(lines[3], "修复解析器  work     2026-10-11 16:05  2026-10-11 17:01  " + sid(3))

    # ---- T22 主目录写成 ~ ----

    def test_tilde(self):
        self.ok("add", "work")
        base = self.acct("work")
        cases = ((1, os.path.join(self.home, "sub"), "cd ~/sub"), (2, self.home, "cd ~"),
                 (3, os.path.join(self.home, "my proj"), "cd ~/'my proj'"), (4, "/w/proj", "cd /w/proj"))
        for number, directory, _ in cases:
            self.rollout(base, sid(number), user("q"), cwd=directory)
        out = self.ok("sessions", "--cmd").out
        by_id = {item["session_id"]: item for item in self.sessions_json()["sessions"]}
        for number, directory, expected in cases:
            with self.subTest(directory=directory):
                self.assertIn("{} && codex-work resume {}".format(expected, sid(number)), out)
                self.assertEqual(by_id[sid(number)]["resume"],
                                 "cd {} && codex-work resume {}".format(shlex.quote(directory), sid(number)))

    # ---- T23 迁移未完成 ----

    def test_pending_migration(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q"))
        self.write(os.path.join(self.state, "migrate-journal.json"), json.dumps(
            {"version": 1, "name": "work", "source": "/x", "target": "/y", "backup": "/z", "mode": "move",
             "phase": "copied"}))
        self.assertIn("unfinished migration", self.run_cli("list").err)
        self.assertIn(sid(1), self.ok("sessions").out)

    # ---- T24 帮助与补全 ----

    def test_help_and_completion(self):
        self.assertIn("sessions", [name for name, _ in dict(cli.COMMAND_GROUPS)["Everyday"]])
        top = self.ok("-h").out
        self.assertIn("sessions", top[top.index("Everyday"):])
        self.assertIn("conversations", self.ok("sessions", "-h").out)
        self.assertIn("sessions", completion.ACCOUNT_COMMANDS)
        # 补全脚本里 sessions 落在“后面补账号名”的那组命令里；别名不参与补全
        script = self.ok("completion", "bash").out
        account_case = next(text for text in script.splitlines() if " rename " in text and " login " in text)
        self.assertIn(" sessions ", account_case + " ")
        self.assertNotIn("conversations", script)

    # ---- T25 只读 ----

    def test_read_only(self):
        self.ok("add", "work")
        self.rollout(self.acct("work"), sid(1), user("q"))
        self.rollout(self.default(), sid(2), user("q"))
        before = self.snapshot(self.home)
        self.ok("sessions")
        self.ok("sessions", "--json", "--cmd")
        self.assertEqual(self.snapshot(self.home), before)


if __name__ == "__main__":
    unittest.main()
