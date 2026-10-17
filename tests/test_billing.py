"""feature-billing-period §8：list -v 的 BILLING UNTIL / CHECKED 两列与 list --json 的 login.subscription。"""

import calendar
import os
import sys
import time
import unittest
from unittest import mock

from helpers import SRC
from test_insight import InsightBase, chatgpt_claims

sys.path.insert(0, SRC)
from multi_codex import identity  # noqa: E402

DAY = 86400
FOOTNOTE = "? the billing period end date has passed"


def iso_utc(epoch):
    """令牌里的写法：带 +00:00 偏移、6 位小数秒（与 §1 实测格式相同）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime(epoch))


def local_day(epoch):
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


class ParseIsoTest(unittest.TestCase):
    """T1：只用 Python 3.8 与 3.11 结果一致的输入。"""

    def test_inputs(self):
        expected = float(calendar.timegm((2026, 9, 21, 2, 36, 15)))
        self.assertEqual(identity._parse_iso("2026-09-21T02:36:15+00:00"), expected)
        self.assertEqual(identity._parse_iso("2026-09-21T02:36:15Z"), expected)
        self.assertAlmostEqual(identity._parse_iso("2026-08-21T05:44:13.926850+00:00"),
                               calendar.timegm((2026, 8, 21, 5, 44, 13)) + 0.92685, places=5)
        for value in ("2026-09-21T02:36:15", "abc", "", 123, None):
            with self.subTest(value=value):
                self.assertIsNone(identity._parse_iso(value))
        # 超出平台时间范围：time 模块抛错时返回 None，不让 list 中断（PR #32 评审意见）。
        with mock.patch.object(identity.time, "localtime", side_effect=OverflowError):
            self.assertIsNone(identity._parse_iso("2026-09-21T02:36:15+00:00"))


class BillingListTest(InsightBase):
    def claims(self, plan="plus", until=None, checked=None):
        claims = chatgpt_claims("w@example.com", plan)
        auth = claims["https://api.openai.com/auth"]
        if until is not None:
            auth["chatgpt_subscription_active_until"] = until
        if checked is not None:
            auth["chatgpt_subscription_last_checked"] = checked
        return claims

    def verbose_row(self, result, name="work"):
        row = next(line for line in result.out.splitlines() if line.split()[:1] == [name])
        return row.split()[7:]

    def test_future_period(self):
        """T2、T8：账期在未来，显示两个本地日期，无 ? 与脚注；JSON 给 UTC；令牌不泄露。"""
        now = time.time()
        until, checked = now + 10 * DAY, now - 5 * DAY
        self.add("work")
        self.write_auth("work", self.claims(until=iso_utc(until), checked=iso_utc(checked)))
        result = self.ok("list", "-v")
        self.assertEqual(self.verbose_row(result), [local_day(until), local_day(checked)])
        self.assertNotIn(FOOTNOTE, result.out)
        self.assert_no_secret(result)
        data, result = self.list_json()
        self.assertEqual(data["accounts"][0]["login"]["subscription"],
                         {"active_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until)),
                          "last_checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(checked))})
        self.assert_no_secret(result)

    def test_passed_period(self):
        """T3：账期结束日已过，加 ? 并在 stdout 输出脚注。"""
        now = time.time()
        until = now - 3 * DAY
        self.add("work")
        self.write_auth("work", self.claims(until=iso_utc(until), checked=iso_utc(now - 30 * DAY)))
        result = self.ok("list", "-v")
        self.assertEqual(self.verbose_row(result)[0], local_day(until) + "?")
        self.assertIn(FOOTNOTE, result.out)
        self.assertNotIn(FOOTNOTE, result.err)

    def test_free_plan_hidden_in_table_only(self):
        """T4：free 在表格里两格为 -，JSON 仍给出日期。"""
        now = time.time()
        until, checked = now - DAY, now - 2 * DAY
        self.add("work")
        self.write_auth("work", self.claims("free", until=iso_utc(until), checked=iso_utc(checked)))
        result = self.ok("list", "-v")
        self.assertEqual(self.verbose_row(result), ["-", "-"])
        self.assertNotIn(FOOTNOTE, result.out)
        data, _ = self.list_json()
        self.assertEqual(data["accounts"][0]["login"]["subscription"],
                         {"active_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until)),
                          "last_checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(checked))})

    def test_only_checked(self):
        """T5：只有核对时间。"""
        checked = time.time() - DAY
        self.add("work")
        self.write_auth("work", self.claims(checked=iso_utc(checked)))
        self.assertEqual(self.verbose_row(self.ok("list", "-v")), ["-", local_day(checked)])
        data, _ = self.list_json()
        self.assertIsNone(data["accounts"][0]["login"]["subscription"]["active_until"])

    def test_missing_or_unparseable(self):
        """T6：两个字段都没有，或者是读不出的字符串。"""
        self.add("none")
        self.write_auth("none", self.claims())
        self.add("bad")
        self.write_auth("bad", self.claims(until="soon", checked="2026-09-21T02:36:15"))
        result = self.ok("list", "-v")
        for name in ("none", "bad"):
            with self.subTest(name=name):
                self.assertEqual(self.verbose_row(result, name), ["-", "-"])
        data, _ = self.list_json()
        for account in data["accounts"]:
            self.assertIsNone(account["login"]["subscription"])

    def test_other_login_types(self):
        """T7：api-key、未登录、keyring、unreadable 两格为 -，JSON 为 null。"""
        self.add("key")
        self.write_auth("key", data={"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"})
        self.add("none")
        self.add("ring")
        # keyring 模式不读 auth.json（identity.py read_identity），这里即使留着带账期的令牌也不应显示。
        self.write(os.path.join(self.account_dir("ring"), "config.toml"), 'cli_auth_credentials_store = "keyring"\n')
        self.write_auth("ring", self.claims(until=iso_utc(time.time() + DAY)))
        self.add("bad")
        self.write_auth("bad", data="{not json")
        result = self.ok("list", "-v")
        for name in ("key", "none", "ring", "bad"):
            with self.subTest(name=name):
                self.assertEqual(self.verbose_row(result, name), ["-", "-"])
        data, _ = self.list_json()
        for account in data["accounts"]:
            self.assertIsNone(account["login"]["subscription"])

    def test_summary_unchanged(self):
        """T9：默认简表不显示账期。"""
        now = time.time()
        self.add("work")
        self.write_auth("work", self.claims(until=iso_utc(now - DAY), checked=iso_utc(now - 2 * DAY)))
        out = self.ok("list").out
        self.assertNotIn("BILLING", out)
        self.assertNotIn(FOOTNOTE, out)


if __name__ == "__main__":
    unittest.main()
