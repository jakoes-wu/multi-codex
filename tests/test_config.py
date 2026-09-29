"""方案 §8 第 3、5 条：代理解析与账号名校验。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from multi_codex.config import normalize_proxy, validate_name  # noqa: E402

from helpers import CliTestCase  # noqa: E402


class ProxyParsingTest(unittest.TestCase):
    def test_valid_values(self):
        self.assertEqual(normalize_proxy("7901"), "http://127.0.0.1:7901")
        self.assertEqual(normalize_proxy("socks5://127.0.0.1:1080"), "socks5://127.0.0.1:1080")
        self.assertEqual(normalize_proxy("http://proxy.local:8080/"), "http://proxy.local:8080")
        self.assertEqual(normalize_proxy("http://[::1]:3128"), "http://[::1]:3128")
        self.assertEqual(normalize_proxy("off"), "off")
        self.assertEqual(normalize_proxy("inherit"), "inherit")

    def test_invalid_values(self):
        for value in ("0", "70000", "ftp://127.0.0.1:21", "http://127.0.0.1:8080/path",
                      "http://user:pw@127.0.0.1:8080", "http://127.0.0.1", "http://:8080", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_proxy(value)


class ProxyCliExitCodeTest(CliTestCase):
    def test_invalid_proxy_on_command_line_returns_2(self):
        for value in ("0", "70000", "ftp://127.0.0.1:21", "http://127.0.0.1:8080/p", "http://u:p@127.0.0.1:1"):
            with self.subTest(value=value):
                self.assertEqual(self.run_cli("add", "work", "--proxy", value).code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.state, "config.json")))

    def test_invalid_proxy_in_config_file_returns_1(self):
        for value in ("0", "70000", "ftp://127.0.0.1:21", "http://127.0.0.1:8080/p", "http://u:p@127.0.0.1:1"):
            with self.subTest(value=value):
                path = self.write(os.path.join(self.tmp, "bad.json"), json.dumps(
                    {"version": 1, "accounts": {"work": {"proxy": value}}}))
                result = self.run_cli("apply", "-f", path)
                self.assertEqual(result.code, 1, result)


class NameTest(CliTestCase):
    def test_rejected_names(self):
        for name in ("-x", ".hidden", "a/b", "a" * 65, ""):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_name(name)
        self.assertEqual(self.run_cli("add", ".hidden").code, 2)
        # "-x" 会先被 argparse 当成未知选项，同样返回 2。
        self.assertEqual(self.run_cli("add", "-x").code, 2)
        self.assertEqual(self.run_cli("add", "--", "-x").code, 2)

    def test_email_names_accepted(self):
        validate_name("someone+test@example.com")

    def test_case_insensitive_duplicate(self):
        self.ok("add", "Work")
        result = self.ok("add", "work")
        self.assertNotIn("create", result.out)
        with open(os.path.join(self.state, "config.json")) as handle:
            accounts = json.load(handle)["accounts"]
        self.assertEqual(list(accounts), ["Work"])


if __name__ == "__main__":
    unittest.main()
