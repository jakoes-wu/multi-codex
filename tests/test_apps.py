"""feature-app-launch §8：按账号打开 VS Code（code）与 Codex 桌面端（app）。

全部用假的 `code`、假的 `open` 和假的应用包，不启动真实应用。
"""

import os
import plistlib
import stat
import sys
import unittest

from helpers import CliTestCase

# 记录收到的参数和关心的环境变量，每行一项。
FAKE_TOOL = """#!/bin/sh
out="$FAKE_TOOL_OUT"
printf '%s\\n' "$@" > "$out.args"
printf 'CODEX_HOME=%s\\nHTTPS_PROXY=%s\\nSECRET=%s\\nRUN_SCRIPT=%s\\n' "$CODEX_HOME" "$HTTPS_PROXY" "$SECRET" \\
  "${MULTI_CODEX_RUN_SCRIPT-unset}" > "$out.env"
exit "${FAKE_TOOL_EXIT:-0}"
"""


class AppsBase(CliTestCase):
    def setUp(self):
        super().setUp()
        self.ok("add", "work", "--proxy", "7901")
        self.ok("env", "work", "SECRET=s3cret-value")
        self.tool_out = os.path.join(self.tmp, "tool")
        self.env["FAKE_TOOL_OUT"] = self.tool_out
        self.fake = self.write(os.path.join(self.tmp, "fake-tool"), FAKE_TOOL, 0o755)

    def args(self):
        with open(self.tool_out + ".args") as handle:
            return handle.read().splitlines()

    def seen_env(self):
        with open(self.tool_out + ".env") as handle:
            return dict(line.split("=", 1) for line in handle.read().splitlines())

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)


class CodeTest(AppsBase):
    def test_code_opens_with_account_environment(self):
        result = self.run_cli("code", "work", "/p", "--bin", self.fake)
        self.assertEqual(result.code, 0, result)
        data_dir = os.path.join(self.root, ".apps", "work", "vscode")
        self.assertEqual(self.args(), ["--user-data-dir", data_dir, "/p"])
        env = self.seen_env()
        self.assertEqual(env["CODEX_HOME"], os.path.join(self.root, "work"))
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7901")
        self.assertEqual(env["SECRET"], "s3cret-value")
        self.assertEqual(env["RUN_SCRIPT"], "unset")
        self.assertEqual(self.mode(data_dir), 0o700)
        self.assertIn("experimental", result.err)

    def test_extra_arguments_after_double_dash(self):
        self.ok("code", "work", "/p", "--bin", self.fake, "--", "--new-window")
        self.assertEqual(self.args()[-1], "--new-window")

    def test_code_not_found(self):
        # PATH 只留测试自己的假命令目录：机器上若装了 VS Code（/usr/bin/code），否则会真的去启动它。
        self.assertEqual(self.run_cli("code", "work", env={"PATH": self.fakebin}).code, 1)
        self.assertEqual(self.run_cli("code", "work", "--bin", "/no/such/code").code, 1)
        not_executable = self.write(os.path.join(self.tmp, "plain"), "x", 0o644)
        self.assertEqual(self.run_cli("code", "work", "--bin", not_executable).code, 1)
        self.assertEqual(self.run_cli("code", "ghost", "--bin", self.fake).code, 1)

    @unittest.skipUnless(sys.platform == "darwin", "the open --env note is printed on macOS only")
    def test_macos_note(self):
        result = self.run_cli("code", "work", "--bin", self.fake)
        self.assertIn("open --env", result.err)


class AppTest(AppsBase):
    def setUp(self):
        super().setUp()
        self.app = os.path.join(self.tmp, "Fake.app")
        self.make_app("com.openai.codex")
        self.env["MULTI_CODEX_TEST_OPEN"] = self.fake

    def make_app(self, bundle_id):
        contents = os.path.join(self.app, "Contents")
        os.makedirs(contents, exist_ok=True)
        with open(os.path.join(contents, "Info.plist"), "wb") as handle:
            plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleExecutable": "Fake"}, handle)

    @unittest.skipUnless(sys.platform == "darwin", "the desktop app is supported on macOS only")
    def test_app_passes_only_codex_home_and_data_dir(self):
        result = self.run_cli("app", "work", "--app", self.app)
        self.assertEqual(result.code, 0, result)
        data_dir = os.path.join(self.root, ".apps", "work", "desktop")
        log = os.path.join(self.root, ".apps", "work", "desktop.log")
        self.assertEqual(self.args(), [
            "-n", "--env", "CODEX_HOME=" + os.path.join(self.root, "work"),
            "--env", "CODEX_ELECTRON_USER_DATA_PATH=" + data_dir,
            "--stdout", log, "--stderr", log, "-a", self.app, "--args", "--user-data-dir=" + data_dir])
        self.assertNotIn("s3cret-value", " ".join(self.args()))
        self.assertEqual(self.mode(log), 0o600)
        self.assertEqual(self.mode(data_dir), 0o700)
        self.assertIn("started Codex desktop for work", result.out)

    @unittest.skipUnless(sys.platform == "darwin", "the desktop app is supported on macOS only")
    def test_app_refusals(self):
        self.assertEqual(self.run_cli("app", "ghost", "--app", self.app).code, 1)
        self.assertEqual(self.run_cli("app", "work", "--app", self.app, env={"FAKE_TOOL_EXIT": "1"}).code, 1)
        os.unlink(self.tool_out + ".args")  # 上一步调用过假 open，清掉它的记录
        os.rename(os.path.join(self.root, "work"), os.path.join(self.tmp, "moved"))
        result = self.run_cli("app", "work", "--app", self.app)
        self.assertEqual(result.code, 1, result)
        self.assertFalse(os.path.exists(self.tool_out + ".args"))  # 账号目录不在时不调用 open
        os.rename(os.path.join(self.tmp, "moved"), os.path.join(self.root, "work"))
        self.make_app("com.openai.chat")
        self.assertEqual(self.run_cli("app", "work", "--app", self.app).code, 1)

    @unittest.skipIf(sys.platform == "darwin", "this checks the refusal on other platforms")
    def test_app_refused_outside_macos(self):
        self.assertEqual(self.run_cli("app", "work", "--app", self.app).code, 1)


class CompletionTest(AppsBase):
    def test_commands_in_completion(self):
        bash = self.ok("completion", "bash").out
        self.assertIn("code", bash)
        self.assertIn("app", bash)
        zsh = self.ok("completion", "zsh").out
        self.assertIn("_files", zsh.split("compadd")[0].split("candidates=(off inherit)")[-1])


if __name__ == "__main__":
    unittest.main()
