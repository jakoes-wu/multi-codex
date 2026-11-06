"""agent-lock 薄集成：默认关闭的启动前缀，不导入 agent_lock 包。"""

import argparse
import errno
import inspect
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

from helpers import IS_WINDOWS, SRC, CliTestCase

sys.path.insert(0, SRC)

from multi_codex import accounts, cli, completion, launcher, router  # noqa: E402
from multi_codex.config import (PROXY_INHERIT, Account, AgentLock, ConfigError, NetCheck,  # noqa: E402
                                default_config, dump_config, parse_config)

POSIX_ONLY = unittest.skipIf(IS_WINDOWS, "POSIX only")
WRAPPER = "/usr/local/sbin/agent-lock"

ID_OK = """#!/bin/sh
if [ "$1" = "agentnet" ]; then
  exit 0
fi
exec /usr/bin/id "$@"
"""

ID_MISSING = """#!/bin/sh
exit 1
"""

INSTALL_OK = """#!/bin/sh
printf '%s\\n' "$@" > "$AGENT_LOCK_ARGV"
exit 0
"""

INSTALL_FAIL = """#!/bin/sh
printf '%s\\n' "$@" > "$AGENT_LOCK_ARGV"
exit 3
"""

EXTRACT_BODY = """import os
import sys

script = os.environ["MULTI_CODEX_RUN_SCRIPT"]
marker = "\\nfi\\n"
index = script.find(marker)
if index < 0:
    sys.exit(9)
body = script[index + len(marker):]
if not body.startswith("unset MULTI_CODEX_RUN_SCRIPT\\n"):
    sys.exit(9)
if not script.endswith(body):
    sys.exit(10)
sys.stdout.write(body)
"""


class ParseTest(unittest.TestCase):
    def test_absent_and_null_are_off(self):
        for raw in ("{}", '{"agent_lock": null}'):
            config = parse_config(raw, "memory")
            self.assertIsNone(config.agent_lock)
            self.assertNotIn("agent_lock", config.to_dict())

    def test_on_shape_and_round_trip(self):
        config = parse_config(json.dumps({"agent_lock": {"enabled": True, "wrapper": WRAPPER}}), "memory")
        self.assertTrue(config.agent_lock.enabled)
        self.assertEqual(config.agent_lock.to_dict(), {"enabled": True, "wrapper": WRAPPER})
        self.assertEqual(config.to_dict()["agent_lock"], {"enabled": True, "wrapper": WRAPPER})
        again = parse_config(dump_config(config), "dump")
        self.assertEqual(again.agent_lock.wrapper, WRAPPER)
        self.assertNotIn("agent_lock", default_config().to_dict())

    def test_rejects_port_secret_and_not_true(self):
        rejected = (
            {"enabled": True, "wrapper": WRAPPER, "port": 7897},
            {"enabled": True, "wrapper": WRAPPER, "secret": "x"},
            {"enabled": False, "wrapper": WRAPPER},
            {"enabled": True, "wrapper": "relative/agent-lock"},
            {"enabled": "true", "wrapper": WRAPPER},
            {"wrapper": WRAPPER},
            "on",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    parse_config(json.dumps({"agent_lock": value}), "memory")

    def test_copy_keeps_its_own_object(self):
        config = default_config()
        config.agent_lock = AgentLock(WRAPPER)
        copied = config.copy()
        self.assertIsNot(copied.agent_lock, config.agent_lock)
        self.assertEqual(copied.agent_lock.wrapper, WRAPPER)
        copied.agent_lock = None
        self.assertEqual(config.agent_lock.wrapper, WRAPPER)

    def test_help_examples_and_completion_choices(self):
        self.assertEqual(cli.COMMAND_SUMMARY["agent-lock"], "turn launchers on or off for agentnet")
        self.assertEqual(cli.COMMAND_EXAMPLES["agent-lock"], (
            ("multi-codex agent-lock", "show status"),
            ("multi-codex agent-lock on", "turn it on"),
            ("multi-codex agent-lock on --wrapper /usr/local/sbin/agent-lock", "set the path"),
            ("multi-codex agent-lock off", "turn it off"),
        ))
        for line in cli._examples_text(cli.COMMAND_EXAMPLES["agent-lock"]).splitlines():
            self.assertLessEqual(len(line), 79)
        self.assertNotIn("agent-lock", completion.ACCOUNT_COMMANDS)
        parser = cli.build_parser()
        positional, option_values = completion.value_spec(parser)
        self.assertEqual(positional[("agent-lock", 0)], ["on", "off"])
        self.assertNotIn(("agent-lock", "--wrapper"), option_values)
        for shell in completion.SHELLS:
            script = completion.script(shell, parser)
            self.assertIn("agent-lock", script)
            needle = "-l wrapper" if shell == "fish" else "--wrapper"
            self.assertIn(needle, script)


class RenderTest(unittest.TestCase):
    def test_off_matches_omitted_wrapper(self):
        account = os.path.join(os.getcwd(), "acct")
        args = ("work", account, "http://127.0.0.1:7901", {"A": "b"}, True)
        off = launcher.render(*args, net_check=True)
        same = launcher.render(*args, net_check=True, wrapper=None)
        self.assertEqual(off, same)
        self.assertNotIn("agentnet", off)
        self.assertNotIn("agent-lock", off)

    def test_file_prefix_after_marker_and_netgate_only_outside(self):
        account = os.path.join(os.getcwd(), "acct")
        text = launcher.render("work", account, PROXY_INHERIT, wrapper=WRAPPER)
        lines = text.splitlines()
        self.assertTrue(lines[1].startswith(launcher.MARKER_PREFIX + "work"))
        self.assertEqual(lines[2], 'if [ "$(/usr/bin/id -un)" != "agentnet" ]; then')
        self.assertIn('  exec {} "$0" "$@"'.format(WRAPPER), lines)
        self.assertNotIn("netgate", text)
        self.assertNotIn("MULTI_CODEX_SKIP_NETCHECK", text)
        self.assertNotIn("command -v multi-codex", text)
        end = lines.index("fi")
        self.assertIn('exec {} "$0" "$@"'.format(WRAPPER), "\n".join(lines[:end]))
        self.assertIn('exec codex "$@"', "\n".join(lines[end:]))
        path = os.path.join(os.getcwd(), "codex-work-agent-lock")
        try:
            with open(path, "w") as handle:
                handle.write(text)
            self.assertEqual(launcher.managed_account(path), "work")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_netgate_stays_in_the_non_agentnet_branch(self):
        account = os.path.join(os.getcwd(), "acct")
        text = launcher.render("work", account, "http://127.0.0.1:7897", net_check=True, wrapper=WRAPPER)
        lines = text.splitlines()
        end = lines.index("fi")
        before, after = "\n".join(lines[:end]), "\n".join(lines[end:])
        self.assertIn("netgate", before)
        self.assertIn("command -v multi-codex", before)
        self.assertIn("unset " + launcher.SKIP_NETCHECK_ENV, before)
        self.assertNotIn("netgate", after)
        self.assertNotIn("command -v multi-codex", after)
        self.assertNotIn(launcher.SKIP_NETCHECK_ENV, after)
        self.assertIn("CODEX_HOME=", after)
        self.assertIn('exec codex "$@"', after)

    def test_command_mode_switch_is_before_unset(self):
        account = os.path.join(os.getcwd(), "acct")
        text = launcher.render("work", account, PROXY_INHERIT, command_mode=True, net_check=True,
                               wrapper=WRAPPER)
        lines = text.splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertEqual(lines[1], 'if [ "$(/usr/bin/id -un)" != "agentnet" ]; then')
        self.assertEqual(lines[2],
                         "  exec {} /bin/sh -c 'eval \"$MULTI_CODEX_RUN_SCRIPT\"' sh \"$@\"".format(WRAPPER))
        self.assertEqual(lines[3], "fi")
        self.assertEqual(lines[4], "unset " + launcher.RUN_SCRIPT_ENV)
        self.assertLess(text.index("exec "), text.index("unset " + launcher.RUN_SCRIPT_ENV))
        self.assertNotIn('"$0"', "\n".join(lines[:5]))
        self.assertNotIn("netgate", text)
        self.assertTrue(lines[5].startswith(launcher.MARKER_PREFIX))

    def test_render_windows_off_bytes_and_on_prefix(self):
        off = launcher.render_windows("a", r"C:\py\python.exe", r"C:\py\_entry.py")
        self.assertEqual(off, "\n".join([
            "@echo off",
            "rem managed-by: multi-codex account=a",
            '"C:\\py\\python.exe" -I "C:\\py\\_entry.py" launch a -- %*',
            "exit /b %ERRORLEVEL%",
            "",
        ]))
        on = launcher.render_windows("a", r"C:\py\python.exe", r"C:\py\_entry.py", agent_lock=True)
        self.assertEqual(on.splitlines()[0], off.splitlines()[0])
        self.assertEqual(on.splitlines()[1], off.splitlines()[1])
        self.assertEqual(on.splitlines()[3], off.splitlines()[3])
        self.assertEqual(
            on.splitlines()[2],
            '"C:\\ProgramData\\agent-lock\\agent-lock.cmd" '
            '"C:\\py\\python.exe" -I "C:\\py\\_entry.py" launch a -- %*')

    def test_router_only_changes_unbound_exec(self):
        empty = parse_config('{"accounts": {}}', "memory")
        self._only_fallback_changes(empty)
        key = os.path.join(os.getcwd(), "project")
        bound = parse_config(json.dumps({"accounts": {"work": {}}, "bindings": {key: "work"}}), "memory")
        on_text = self._only_fallback_changes(bound)
        self.assertIn('exec "$target" "$@"', on_text)

    def _only_fallback_changes(self, config):
        off = router.render(config)
        config.agent_lock = AgentLock(WRAPPER)
        on = router.render(config)
        self.assertEqual(off.splitlines()[:-1], on.splitlines()[:-1])
        self.assertEqual(off.splitlines()[-1], 'exec codex "$@"')
        self.assertEqual(on.splitlines()[-1], "exec {} codex \"$@\"".format(WRAPPER))
        self.assertNotIn('exec codex "$@"', on)
        return on


@POSIX_ONLY
class CommandModeReentryTest(CliTestCase):
    def test_fake_switcher_reenters_once_and_keeps_the_body(self):
        account = os.path.join(self.tmp, "acct")
        os.makedirs(account)
        count = os.path.join(self.tmp, "count")
        extract = os.path.join(self.tmp, "extract_body.py")
        switcher = os.path.join(self.tmp, "switcher")
        with open(extract, "w") as handle:
            handle.write(EXTRACT_BODY)
        script = """#!/bin/sh
n=0
if [ -f {count} ]; then
  n=$(cat {count})
fi
n=$((n + 1))
printf '%s\\n' "$n" > {count}
if [ "$n" != 1 ]; then
  echo "re-entered $n" >&2
  exit 8
fi
body=$({python} {extract})
status=$?
if [ "$status" != 0 ]; then
  exit "$status"
fi
MULTI_CODEX_RUN_SCRIPT="$body"
export MULTI_CODEX_RUN_SCRIPT
shift 4
exec /bin/sh -c 'eval "$MULTI_CODEX_RUN_SCRIPT"' sh "$@"
""".format(count=shlex.quote(count), python=shlex.quote(sys.executable), extract=shlex.quote(extract))
        self.write(switcher, script, 0o755)
        text = launcher.render("work", account, PROXY_INHERIT, command_mode=True, wrapper=switcher)
        lines = text.splitlines()
        exec_at = next(index for index, line in enumerate(lines) if line.startswith("  exec "))
        unset_at = lines.index("unset " + launcher.RUN_SCRIPT_ENV)
        self.assertLess(exec_at, unset_at)
        marker = "\nfi\n"
        body = text[text.find(marker) + len(marker):]
        self.assertTrue(body.startswith("unset MULTI_CODEX_RUN_SCRIPT\n"))
        self.assertTrue(text.endswith(body))
        env = {"PATH": os.defpath, "HOME": self.home, "MULTI_CODEX_RUN_SCRIPT": text}
        proc = subprocess.run(
            ["/bin/sh", "-c", 'eval "$MULTI_CODEX_RUN_SCRIPT"', "sh", "/bin/sh", "-c",
             'printf %s "$CODEX_HOME"'],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, account)
        with open(count) as handle:
            self.assertEqual(handle.read().strip(), "1")
        self.assertIn(body, text)
        self.assertLess(text.index("  exec "), text.index("unset " + launcher.RUN_SCRIPT_ENV))


@POSIX_ONLY
class ModeTest(CliTestCase):
    def test_mode_rules(self):
        if sys.platform.startswith("linux"):
            self.assertEqual(launcher.launcher_mode({"A": "b"}, agent_lock=True), 0o750)
        else:
            self.assertEqual(launcher.launcher_mode({"A": "b"}, agent_lock=True), 0o700)
        self.assertEqual(launcher.launcher_mode({"A": "b"}), 0o700)
        self.assertEqual(launcher.launcher_mode(None, agent_lock=True), 0o755)
        with mock.patch.object(launcher.sys, "platform", "linux"):
            self.assertEqual(launcher.launcher_mode({"A": "b"}, agent_lock=True), 0o750)
            self.assertEqual(launcher.launcher_mode(None, agent_lock=True), 0o755)
            self.assertEqual(launcher.launcher_mode({"A": "b"}, agent_lock=False), 0o700)

    def test_file_ok_linux_requires_exact_owner_gid_and_mode(self):
        content = "#!/bin/sh\n"
        directory = os.path.join(self.tmp, "plain")
        os.makedirs(directory)
        self.assertFalse(os.stat(directory).st_mode & stat.S_ISGID)
        path = self.write(os.path.join(directory, "codex-work"), content, 0o750)
        file_gid = os.stat(path).st_gid
        with mock.patch.object(launcher.sys, "platform", "linux"), \
                mock.patch.object(launcher, "_agentnet_gid", return_value=file_gid + 1):
            self.assertFalse(launcher.launcher_file_ok(path, content, {"TOKEN": "x"}, agent_lock=True))
        with mock.patch.object(launcher.sys, "platform", "linux"), \
                mock.patch.object(launcher, "_agentnet_gid", return_value=file_gid):
            self.assertTrue(launcher.launcher_file_ok(path, content, {"TOKEN": "x"}, agent_lock=True))
            os.chmod(path, 0o700)
            self.assertFalse(launcher.launcher_file_ok(path, content, {"TOKEN": "x"}, agent_lock=True))
        os.chmod(path, 0o755)
        with mock.patch.object(launcher.sys, "platform", "linux"):
            self.assertTrue(launcher.launcher_file_ok(path, content, None, agent_lock=True))
            self.assertFalse(launcher.launcher_file_ok(path, content + "x", None, agent_lock=True))
        if not sys.platform.startswith("linux"):
            os.chmod(path, 0o700)
            self.assertTrue(launcher.launcher_file_ok(path, content, {"TOKEN": "x"}, agent_lock=True))
            os.chmod(path, 0o750)
            self.assertFalse(launcher.launcher_file_ok(path, content, {"TOKEN": "x"}, agent_lock=True))

    def test_plan_refuses_unbound_directory_and_does_not_chown(self):
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        with mock.patch.object(launcher.sys, "platform", "linux"):
            self.assertIsNotNone(launcher.linux_bind_dir_error(bin_dir))
            actions = accounts._plan_launcher(bin_dir, "work", os.path.join(self.tmp, "acct"), PROXY_INHERIT,
                                              {"TOKEN": "x"}, False, WRAPPER)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].status, "conflict")
        self.assertEqual(actions[0].reason, "sudo agent-lock bind-bin {}".format(bin_dir))
        self.assertIsNone(actions[0].run)
        self.assertFalse(os.path.exists(os.path.join(bin_dir, "codex-work")))
        self.assertNotIn("os.chown", inspect.getsource(accounts._write_launcher))

    def test_plan_writes_0750_without_chown_when_parent_is_accepted(self):
        bin_dir = os.path.join(self.tmp, "bound")
        os.makedirs(bin_dir)
        with mock.patch.object(launcher.sys, "platform", "linux"), \
                mock.patch.object(launcher, "linux_bind_dir_error", return_value=None):
            actions = accounts._plan_launcher(bin_dir, "work", os.path.join(self.tmp, "acct"), PROXY_INHERIT,
                                              {"TOKEN": "x"}, False, WRAPPER)
        self.assertEqual(actions[0].status, "create")
        actions[0].run()
        path = os.path.join(bin_dir, "codex-work")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o750)
        self.assertNotIn("os.chown", inspect.getsource(accounts._write_launcher))

    def test_setgid_file_gid_follows_directory(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("setgid inheritance is the Linux model under test")
        try:
            import grp
            gid = grp.getgrnam("agentnet").gr_gid
        except (ImportError, KeyError):
            self.skipTest("agentnet group is not installed")
        directory = os.path.join(self.tmp, "sgid")
        os.makedirs(directory)
        try:
            os.chown(directory, -1, gid)
            os.chmod(directory, stat.S_ISGID | 0o750)
        except OSError:
            self.skipTest("this process cannot make a setgid agentnet directory")
        info = os.stat(directory)
        if not (stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISGID and info.st_gid == gid
                and not info.st_mode & stat.S_IWGRP):
            self.skipTest("directory did not stay a bind-bin directory")
        target = os.path.join(directory, "codex-work")
        accounts._write_launcher(target, "#!/bin/sh\n", 0o750)()
        written = os.stat(target)
        self.assertEqual(written.st_gid, gid)
        self.assertEqual(stat.S_IMODE(written.st_mode), 0o750)

    def test_chown_to_agentnet_is_eperm_when_the_os_enforces_it(self):
        try:
            import grp
            gid = grp.getgrnam("agentnet").gr_gid
        except (ImportError, KeyError):
            self.skipTest("agentnet group is not installed")
        path = self.write(os.path.join(self.tmp, "owned"), "x", 0o600)
        try:
            os.chown(path, -1, gid)
        except OSError as exc:
            self.assertIn(exc.errno, (errno.EPERM, errno.EACCES))
            return
        self.skipTest("this process can chown to agentnet")


class LaunchWindowsTest(CliTestCase):
    def _config(self, locked=True):
        config = default_config()
        config.root = self.root
        config.accounts = {"work": Account("work")}
        os.makedirs(os.path.join(self.root, "work"))
        if locked:
            config.agent_lock = AgentLock(launcher.WINDOWS_AGENT_LOCK_CMD)
        return config

    def test_unbound_refuses_non_agentnet_and_prefixes_when_agentnet(self):
        config = self._config()
        with mock.patch.object(cli.platform, "IS_WINDOWS", True), \
                mock.patch.object(cli, "load_config", return_value=(config, True)), \
                mock.patch.object(cli.binding, "normalize_dir", return_value=os.path.join(self.tmp, "here")), \
                mock.patch.object(cli.binding, "resolve", return_value=None), \
                mock.patch.object(cli, "_windows_is_agentnet", return_value=False), \
                mock.patch.object(cli, "_call_windows") as call:
            buf = io.StringIO()
            with redirect_stderr(buf):
                code = cli.cmd_launch(["--"])
        self.assertEqual(code, 126)
        call.assert_not_called()
        self.assertIn("runas /user:agentnet", buf.getvalue())
        self.assertNotIn("running codex", buf.getvalue())

        with mock.patch.object(cli.platform, "IS_WINDOWS", True), \
                mock.patch.object(cli, "load_config", return_value=(config, True)), \
                mock.patch.object(cli.binding, "normalize_dir", return_value=os.path.join(self.tmp, "here")), \
                mock.patch.object(cli.binding, "resolve", return_value=None), \
                mock.patch.object(cli, "_windows_is_agentnet", return_value=True), \
                mock.patch.object(cli, "_call_windows", return_value=0) as call:
            buf = io.StringIO()
            with redirect_stderr(buf):
                code = cli.cmd_launch(["--", "extra"])
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args[0][1], [launcher.WINDOWS_AGENT_LOCK_CMD, "codex", "extra"])
        self.assertIn("running codex", buf.getvalue())

    def test_unbound_off_stays_plain_codex(self):
        config = self._config(locked=False)
        with mock.patch.object(cli.platform, "IS_WINDOWS", True), \
                mock.patch.object(cli, "load_config", return_value=(config, True)), \
                mock.patch.object(cli.binding, "normalize_dir", return_value=os.path.join(self.tmp, "here")), \
                mock.patch.object(cli.binding, "resolve", return_value=None), \
                mock.patch.object(cli, "_call_windows", return_value=0) as call:
            code = cli.cmd_launch(["--"])
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args[0][1], ["codex"])

    def test_named_launch_checks_user_and_does_not_double_wrap(self):
        config = self._config()
        with mock.patch.object(cli.platform, "IS_WINDOWS", True), \
                mock.patch.object(cli, "load_config", return_value=(config, True)), \
                mock.patch.object(cli, "_windows_is_agentnet", return_value=False), \
                mock.patch.object(cli, "_call_windows") as call:
            buf = io.StringIO()
            with redirect_stderr(buf):
                code = cli.cmd_launch(["work", "--"])
        self.assertEqual(code, 126)
        call.assert_not_called()
        self.assertIn("runas /user:agentnet", buf.getvalue())

        with mock.patch.object(cli.platform, "IS_WINDOWS", True), \
                mock.patch.object(cli, "load_config", return_value=(config, True)), \
                mock.patch.object(cli, "_windows_is_agentnet", return_value=True), \
                mock.patch.object(cli, "_call_windows", return_value=0) as call:
            code = cli.cmd_launch(["work", "--", "extra"])
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args[0][1], ["codex", "extra"])

    def test_windows_on_refuses_strict_and_clash_without_changing_them(self):
        for net_check in (NetCheck(level="strict"), NetCheck(clash_tun=True)):
            config = default_config()
            config.accounts = {"work": Account("work", net_check=net_check)}
            new = config.copy()
            wrapper = self.write(os.path.join(self.tmp, "switcher.cmd"), "@echo off\r\n", 0o755)
            args = argparse.Namespace(action="on", wrapper=wrapper)
            with mock.patch.object(cli.platform, "IS_WINDOWS", True), \
                    mock.patch.object(cli, "_agentnet_account_ok", return_value=True), \
                    mock.patch.object(cli, "_install_trust", return_value=True) as install, \
                    mock.patch.object(cli.accounts, "converge") as converge:
                code = cli._agent_lock_on(config, new, True, args)
            self.assertEqual(code, 1)
            install.assert_not_called()
            converge.assert_not_called()
            self.assertIsNone(new.agent_lock)
            kept = new.accounts["work"].net_check
            self.assertEqual(kept.level, net_check.level)
            self.assertEqual(kept.clash, net_check.clash)


@POSIX_ONLY
class CliTest(CliTestCase):
    def setUp(self):
        super(CliTest, self).setUp()
        self.argv_path = os.path.join(self.tmp, "install-argv")
        self.touch_path = os.path.join(self.tmp, "wrapper-touched")
        self.env["AGENT_LOCK_ARGV"] = self.argv_path
        self.env["WRAPPER_TOUCH"] = self.touch_path
        self.wrapper = os.path.join(self.tmp, "switcher")

    def config(self):
        with open(os.path.join(self.state, "config.json")) as handle:
            return json.load(handle)

    def launcher_text(self, name="work"):
        with open(os.path.join(self.bin, "codex-" + name)) as handle:
            return handle.read()

    def write_exe(self, path, body):
        self.write(path, body, 0o755)

    def prepare(self, identity=ID_OK, install=INSTALL_OK, comment=True):
        self.write_exe(os.path.join(self.fakebin, "id"), identity)
        installer = os.path.join(self.fakebin, "agent-lock")
        if install is None:
            if os.path.exists(installer):
                os.remove(installer)
        else:
            self.write_exe(installer, install)
        lines = ["#!/bin/sh"]
        if comment:
            lines.append("# proxy_url=http://127.0.0.1:7897")
        lines.append('if [ "$2" = "-c" ]; then exit "${PROBE_EXIT:-1}"; fi')
        lines.append('if [ -n "$WRAPPER_TOUCH" ]; then : > "$WRAPPER_TOUCH"; fi')
        lines.append("exit 0")
        self.write_exe(self.wrapper, "\n".join(lines) + "\n")

    def test_show_is_local_and_does_not_run_the_wrapper(self):
        result = self.ok("agent-lock")
        self.assertEqual(result.out, "enabled: no\nwrapper:\n")
        self.assertFalse(os.path.exists(os.path.join(self.state, "config.json")))
        self.prepare()
        self.ok("add", "work")
        state = os.path.join(self.home, ".config", "agent-lock")
        os.makedirs(state)
        with open(os.path.join(state, "state.json"), "w") as handle:
            handle.write("{")
        data = self.config()
        data["agent_lock"] = {"enabled": True, "wrapper": self.wrapper}
        with open(os.path.join(self.state, "config.json"), "w") as handle:
            json.dump(data, handle)
        shown = self.ok("agent-lock")
        self.assertEqual(shown.out, "enabled: yes\nwrapper: {}\n".format(self.wrapper))
        self.assertFalse(os.path.exists(self.touch_path))

    def test_on_off_and_proxy_keeps_the_prefix(self):
        self.prepare()
        self.ok("add", "work")
        before = self.launcher_text()
        self.ok("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertFalse(os.path.exists(self.touch_path))
        saved = self.config()
        self.assertEqual(saved["agent_lock"], {"enabled": True, "wrapper": self.wrapper})
        text = self.launcher_text()
        self.assertTrue(text.splitlines()[1].startswith("# managed-by: multi-codex account=work"))
        self.assertIn('exec {} "$0" "$@"'.format(self.wrapper), text)
        self.assertNotIn("netgate", text)
        self.assertEqual(os.stat(os.path.join(self.bin, "codex-work")).st_mode & 0o777, 0o755)
        with open(self.argv_path) as handle:
            argv = handle.read().splitlines()
        self.assertEqual(argv[0], "install")
        self.assertEqual(argv.count("--trust-exec"), 2)
        self.assertEqual(argv[2], sys.executable)
        self.assertEqual(argv[4], launcher.entry_script())
        listed = json.loads(self.ok("list", "--json").out)
        self.assertEqual(listed["accounts"][0]["launcher"], "ok")
        self.ok("proxy", "work", "7897")
        self.assertEqual(self.config()["agent_lock"]["wrapper"], self.wrapper)
        rewritten = self.launcher_text()
        self.assertIn("agentnet", rewritten)
        self.assertIn("http://127.0.0.1:7897", rewritten)
        self.ok("agent-lock", "on")
        self.assertEqual(self.config()["agent_lock"]["wrapper"], self.wrapper)
        self.ok("agent-lock", "off")
        self.assertNotIn("agent_lock", self.config())
        with open(os.path.join(self.state, "config.json")) as handle:
            self.assertNotIn("agent_lock", handle.read())
        # proxy was changed, so off does not return to the original add text; the prefix is gone.
        self.assertNotIn("agentnet", self.launcher_text())
        self.assertNotEqual(before, self.launcher_text())
        self.ok("agent-lock", "off")

    def test_apply_round_trip_keeps_wrapper(self):
        self.prepare()
        self.ok("add", "work")
        self.ok("agent-lock", "on", "--wrapper", self.wrapper)
        path = os.path.join(self.tmp, "apply.json")
        with open(path, "w") as handle:
            json.dump({"accounts": {"work": {"proxy": "inherit"}}}, handle)
        result = self.ok("apply", "-f", path)
        self.assertNotIn("'agent_lock' in", result.err)
        self.assertEqual(self.config()["agent_lock"]["wrapper"], self.wrapper)
        self.assertIn(self.wrapper, self.launcher_text())
        other = os.path.join(self.tmp, "other-switch")
        payloads = (
            {"enabled": True, "wrapper": other},
            {"enabled": False, "wrapper": other},
            {"enabled": True, "wrapper": other, "port": 1},
        )
        for payload in payloads:
            with self.subTest(payload=sorted(payload)):
                with open(path, "w") as handle:
                    json.dump({"accounts": {"work": {"proxy": "inherit"}}, "agent_lock": payload}, handle)
                result = self.ok("apply", "-f", path)
                self.assertIn("'agent_lock' in", result.err)
                self.assertIn("multi-codex agent-lock on|off", result.err)
                self.assertEqual(self.config()["agent_lock"]["wrapper"], self.wrapper)
                text = self.launcher_text()
                self.assertIn(self.wrapper, text)
                self.assertNotIn(other, text)

    def test_preflight_failures_do_not_rewrite(self):
        self.ok("add", "work")
        before = self.launcher_text()
        result = self.run_cli("agent-lock", "on", "--wrapper", "relative")
        self.assertEqual(result.code, 1, result)
        self.assertEqual(result.out, "")
        self.assertEqual(self.launcher_text(), before)

        plain = self.write(os.path.join(self.tmp, "noexec"), "#!/bin/sh\n", 0o644)
        result = self.run_cli("agent-lock", "on", "--wrapper", plain)
        self.assertEqual(result.code, 1, result)
        self.assertEqual(self.launcher_text(), before)
        self.assertNotIn("agent_lock", self.config())

        self.prepare(identity=ID_MISSING)
        result = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertEqual(result.code, 1, result)
        self.assertIn("agentnet", result.err)
        self.assertFalse(os.path.exists(self.argv_path))
        self.assertEqual(self.launcher_text(), before)

        self.prepare(install=None)
        result = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertEqual(result.code, 1, result)
        self.assertIn("not on PATH", result.err)
        self.assertEqual(self.launcher_text(), before)
        self.assertNotIn("agent_lock", self.config())

        self.prepare(install=INSTALL_FAIL)
        result = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertEqual(result.code, 1, result)
        self.assertIn("install exited", result.err)
        self.assertEqual(self.launcher_text(), before)

    def test_proxy_rules(self):
        self.prepare()
        self.ok("add", "work")
        self.ok("add", "home")
        self.ok("proxy", "home", "off")
        before_work = self.launcher_text("work")
        before_home = self.launcher_text("home")
        result = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertEqual(result.code, 1, result)
        self.assertIn("proxy is off", result.err)
        self.assertFalse(os.path.exists(self.argv_path))
        self.assertEqual(self.launcher_text("work"), before_work)
        self.assertEqual(self.launcher_text("home"), before_home)

        self.ok("proxy", "home", "7901")
        result = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertEqual(result.code, 1, result)
        self.assertIn("proxy port", result.err)
        self.assertNotIn("agent_lock", self.config())

        self.ok("proxy", "home", "inherit")
        self.prepare(comment=False)
        self.ok("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertEqual(self.config()["agent_lock"]["wrapper"], self.wrapper)

        self.ok("agent-lock", "off")
        self.prepare()
        self.ok("proxy", "work", "socks5://127.0.0.1:7897")
        self.ok("agent-lock", "on", "--wrapper", self.wrapper)
        self.assertIn("agentnet", self.launcher_text())

    def test_posix_allows_strict_and_http_clash(self):
        self.prepare()
        self.ok("add", "work")
        self.ok("proxy", "work", "7897")
        self.ok("clash-api", "127.0.0.1:9097")
        self.ok("netcheck", "work", "strict", "--clash-tun", "on")
        self.ok("agent-lock", "on", "--wrapper", self.wrapper)
        saved = self.config()
        self.assertEqual(saved["accounts"]["work"]["net_check"]["level"], "strict")
        self.assertTrue(saved["accounts"]["work"]["net_check"]["clash_tun"])
        self.assertEqual(saved["clash_api"]["address"], "http://127.0.0.1:9097")
        self.assertEqual(saved["agent_lock"]["wrapper"], self.wrapper)
        text = self.launcher_text()
        end = text.splitlines().index("fi")
        self.assertIn("netgate", "\n".join(text.splitlines()[:end]))
        self.assertNotIn("netgate", "\n".join(text.splitlines()[end:]))

    def test_linux_env_needs_bind_bin_darwin_keeps_0700(self):
        self.prepare()
        self.ok("add", "work")
        self.ok("env", "work", "TOKEN=secret")
        before = self.launcher_text()
        if sys.platform.startswith("linux"):
            result = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper)
            self.assertEqual(result.code, 1, result)
            self.assertIn("sudo agent-lock bind-bin {}".format(self.bin), result.err)
            self.assertFalse(os.path.exists(self.argv_path))
            self.assertEqual(self.launcher_text(), before)
            self.assertNotIn("agent_lock", self.config())
        else:
            self.ok("agent-lock", "on", "--wrapper", self.wrapper)
            mode = os.stat(os.path.join(self.bin, "codex-work")).st_mode
            self.assertEqual(stat.S_IMODE(mode), 0o700)
            self.assertEqual(stat.S_IMODE(mode) & 0o077, 0)
            self.assertIn("agentnet", self.launcher_text())

    def test_unix_socket_probe_fail_closed(self):
        self.prepare()
        self.ok("add", "work")
        sock = os.path.join(self.tmp, "controller.sock")
        self.write(sock, "x")
        mode = os.stat(sock).st_mode
        self._state({"controller": "unix:" + sock, "owner_home": os.path.join(self.tmp, "owner"),
                     "wrapper": WRAPPER})
        refused = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper, env={"PROBE_EXIT": "0"})
        self.assertEqual(refused.code, 1, refused)
        self.assertIn("unix controller", refused.err)
        self.assertEqual(os.stat(sock).st_mode, mode)
        self.assertNotIn("agent_lock", self.config())
        self.assertFalse(os.path.exists(self.argv_path))

        other = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper, env={"PROBE_EXIT": "2"})
        self.assertEqual(other.code, 1, other)
        self.assertEqual(os.stat(sock).st_mode, mode)

        self.ok("agent-lock", "on", "--wrapper", self.wrapper, env={"PROBE_EXIT": "1"})
        self.assertEqual(self.config()["agent_lock"]["wrapper"], self.wrapper)
        self.ok("agent-lock", "off")

        os.unlink(os.path.join(self.home, ".config", "agent-lock", "state.json"))
        xdg = os.path.join(self.tmp, "xdg")
        self._state({"controller": "unix:" + sock}, xdg=xdg)
        via_xdg = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper,
                               env={"XDG_CONFIG_HOME": xdg, "PROBE_EXIT": "0"})
        self.assertEqual(via_xdg.code, 1, via_xdg)
        self.assertEqual(os.stat(sock).st_mode, mode)

        owner = os.path.join(self.tmp, "owner")
        mihomo = os.path.join(owner, ".local", "share", "mihomo-cli", "mihomo.sock")
        self.write(mihomo, "x")
        mihomo_mode = os.stat(mihomo).st_mode
        self._state({"controller": "http://127.0.0.1:9090", "owner_home": owner}, xdg=xdg)
        http_open = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper,
                                 env={"XDG_CONFIG_HOME": xdg, "PROBE_EXIT": "0"})
        self.assertEqual(http_open.code, 1, http_open)
        self.assertEqual(os.stat(mihomo).st_mode, mihomo_mode)

        self._state({"controller": "http://127.0.0.1:9090", "owner_home": os.path.join(self.tmp, "absent")},
                    xdg=xdg)
        self.ok("proxy", "work", "7897")
        self.ok("netcheck", "work", "strict")
        self.ok("agent-lock", "on", "--wrapper", self.wrapper, env={"XDG_CONFIG_HOME": xdg, "PROBE_EXIT": "0"})
        self.assertEqual(self.config()["accounts"]["work"]["net_check"]["level"], "strict")

        self.ok("agent-lock", "off")
        self._state("{", xdg=xdg)
        broken = self.run_cli("agent-lock", "on", "--wrapper", self.wrapper, env={"XDG_CONFIG_HOME": xdg})
        self.assertEqual(broken.code, 1, broken)
        self.assertIn("state", broken.err)
        self.assertNotIn("agent_lock", self.config())

    def _state(self, document, xdg=None):
        base = os.path.join(xdg, "agent-lock") if xdg else os.path.join(self.home, ".config", "agent-lock")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "state.json"), "w") as handle:
            if isinstance(document, str):
                handle.write(document)
            else:
                json.dump(document, handle)
